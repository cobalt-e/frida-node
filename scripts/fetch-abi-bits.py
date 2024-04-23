from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


IMAGE_ARCHIVE_START = b"!<arch>\n"
IMAGE_FILE_MACHINE_UNKNOWN = 0
IMPORT_OBJECT_HDR_SIG2 = 0xffff


def main(argv):
    runtime, target, gyp_os, gyp_arch = argv[1:5]
    flavor = "|".join(argv[1:5])
    node, npm, outdir = [Path(p) for p in argv[5:8]]

    abidir = outdir / "abi-bits"
    metadata_file = abidir / "abi-bits.json"

    metadata = None
    if metadata_file.exists():
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if metadata["flavor"] != flavor:
            metadata = None

    if metadata is None:
        if abidir.exists():
            shutil.rmtree(abidir)

        (node_incdirs, node_gypdir, node_libs) = load_dev_assets(runtime, target, gyp_os, gyp_arch,
                                                                 node, outdir, abidir)

        subprocess.run([npm, "init", "-y"],
                       capture_output=True,
                       cwd=abidir,
                       check=True)
        subprocess.run([npm, "install", "node-abi", "node-gyp"],
                       capture_output=True,
                       cwd=abidir,
                       check=True)

        abi = subprocess.run([node, "-e", f"console.log(require('node-abi').getAbi('{target}', '{runtime}'))"],
                             capture_output=True,
                             encoding="utf-8",
                             cwd=abidir,
                             check=True).stdout.strip()

        node_defines = load_node_defines(gyp_os, gyp_arch, node_gypdir,
                                         abidir / "node_modules" / "node-gyp" / "gyp" / "pylib")

        node_incdirs_rel = [d.relative_to(outdir) if d.is_relative_to(outdir) else d for d in node_incdirs]
        node_libs_rel    = [l.relative_to(outdir) if l.is_relative_to(outdir) else l for l in node_libs]

        metadata = {
            "flavor": flavor,
            "abi": abi,
            "node_defines": node_defines,
            "node_incdirs": [str(d) for d in node_incdirs_rel],
            "node_libs": [str(l) for l in node_libs_rel],
        }
        metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print_metadata(metadata)


def print_metadata(metadata):
    print("abi:", metadata["abi"])
    print("node_defines:", " ".join(metadata["node_defines"]))
    for d in metadata["node_incdirs"]:
        print("node_incdir:", d)
    for l in metadata["node_libs"]:
        print("node_lib:", l)


def load_dev_assets(runtime, target, gyp_os, gyp_arch, node, outdir, abidir):
    if runtime == "node" and target == "" and gyp_os != "win":
        node_incroot = node.parent.parent / "include"
        node_incdir = node_incroot / "node"
        if node_incdir.exists():
            abidir.mkdir()
            node_gypdir = node_incdir
            node_libs = []
            return ([node_incdir, node_incroot], node_gypdir, node_libs)

    if target == "":
        version = subprocess.run([node, "--version"], capture_output=True, encoding="utf-8").stdout.strip()
    else:
        version = f"v{target}"

    if runtime == "node":
        base_url = f"https://nodejs.org/dist/{version}"
        headers_stem = f"node-{version}-headers"
        libs_subpath = f"/win-{gyp_arch}"
        compression_formats = ["xz", "gz"]
    elif runtime == "electron":
        base_url = f"https://electronjs.org/headers/{version}"
        headers_stem = f"node-{version}-headers"
        libs_subpath = f"/win-{gyp_arch}"
        compression_formats = ["gz"]
    else:
        assert runtime == "node-webkit"
        base_url = f"https://node-webkit.s3.amazonaws.com/{version}"
        headers_stem = f"nw-headers-{version}"
        libs_subpath = "" if gyp_arch == "ia32" else f"/{gyp_arch}"
        compression_formats = ["gz"]

    download_error = None
    for compression in compression_formats:
        try:
            with urllib.request.urlopen(f"{base_url}/{headers_stem}.tar.{compression}") as response:
                tar_blob = response.read()
        except urllib.error.HTTPError as e:
            download_error = e
            if e.code == 404:
                continue
            raise e

        with tarfile.open(fileobj=BytesIO(tar_blob), mode=f"r:{compression}") as tar:
            extracted_rootdir_name = tar.getnames()[0].split("/", maxsplit=1)[0]
            tar.extractall(outdir)

        download_error = None
        break
    if download_error is not None:
        print(download_error, file=sys.stderr)
        sys.exit(1)

    extracted_rootdir = outdir / extracted_rootdir_name

    node_libnames = []
    if gyp_os == "win":
        libdir = extracted_rootdir / "lib"
        libdir.mkdir()

        node_lib = libdir / "node.lib"
        with urllib.request.urlopen(f"{base_url}{libs_subpath}/node.lib") as response:
            vanilla_lib = response.read()
            redacted_lib = BytesIO(vanilla_lib)
            redact_node_lib_symbols(redacted_lib)
            node_lib.write_bytes(redacted_lib.getvalue())
        node_libnames.append(node_lib.name)

        if runtime == "node-webkit":
            nw_lib = libdir / "nw.lib"
            with urllib.request.urlopen(f"{base_url}{libs_subpath}/nw.lib") as response:
                nw_lib.write_bytes(response.read())
            node_libnames.append(nw_lib.name)

    os.rename(extracted_rootdir, abidir)

    if runtime == "node-webkit":
        node_incdirs = [
            abidir / "src",
            abidir / "deps" / "uv" / "include",
            abidir / "deps" / "v8" / "include",
        ]
        node_gypdir = abidir
    else:
        incdir = abidir / "include" / "node"
        node_incdirs = [incdir]
        node_gypdir = incdir

    node_libs = [abidir / "lib" / name for name in node_libnames]

    return (node_incdirs, node_gypdir, node_libs)


def load_node_defines(gyp_os, gyp_arch, node_gypdir, gyp_pylib):
    sys.path.insert(0, str(gyp_pylib))
    import gyp

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as binding_gyp:
        binding_gyp.write("""{
  "targets": [
    {
      "target_name": "frida_binding",
      "type": "loadable_module",
      "sources": [
        "src/addon.cc",
      ],
    },
  ],
}
""")
        binding_gyp.close()
        try:
            [generator, flat_list, targets, data] = \
                    gyp.Load([binding_gyp.name],
                             "compile_commands_json",
                             default_variables={
                                 "OS": gyp_os,
                                 "target_arch": gyp_arch,
                                 "MSVS_VERSION": "auto",
                                 "node_engine": "v8",
                             },
                             includes=[
                                 node_gypdir / "common.gypi",
                                 node_gypdir / "config.gypi",
                             ],
                             params={
                                 "options": GypOptions(),
                                 "parallel": False,
                                 "root_targets": None,
                             })
        finally:
            os.unlink(binding_gyp.name)

    target = targets[flat_list[0]]
    config = target["configurations"][target["default_configuration"]]
    return [adapt_node_define(d) for d in config["defines"] if want_node_define(d)]


def want_node_define(d):
    if d.startswith("V8_") and "DEPRECATION_WARNINGS" in d:
        return False
    return True


def adapt_node_define(d):
    if d.startswith("BUILDING_"):
        return "USING_" + d[9:]
    if d == "_HAS_EXCEPTIONS=1":
        return "_HAS_EXCEPTIONS=0"
    return d


class GypOptions:
    generator_output = os.getcwd()


def redact_node_lib_symbols(lib):
    magic = lib.read(8)
    assert magic == IMAGE_ARCHIVE_START

    file_header = read_image_archive_member_header(lib)

    num_symbols, = struct.unpack(">I", lib.read(4))

    symbol_offsets = []
    for i in range(num_symbols):
        sym_offset, = struct.unpack(">I", lib.read(4))
        symbol_offsets.append(sym_offset)
    symbol_offsets = list(sorted(set(symbol_offsets)))

    string_pool_start = lib.tell()
    string_pool_end = symbol_offsets[0]

    renamed_symbols = {}
    for offset in symbol_offsets:
        lib.seek(offset)

        member_header = read_image_archive_member_header(lib)
        object_header = read_import_object_header(lib)

        if object_header.sig1 == IMAGE_FILE_MACHINE_UNKNOWN and \
                object_header.sig2 == IMPORT_OBJECT_HDR_SIG2:
            import_name_offset = lib.tell()
            strings = lib.read(object_header.size_of_data).split(b"\x00")
            import_name = strings[0]
            dll_name = strings[1]
            is_node_symbol = import_name.startswith(b"?") \
                    or import_name.startswith(b"napi_") \
                    or import_name.startswith(b"node") \
                    or import_name.startswith(b"uv_")
            if not is_node_symbol:
                new_prefix = b"X" if not import_name.startswith(B"X") else b"Y"
                redacted_name = new_prefix + import_name[1:]
                lib.seek(import_name_offset)
                lib.write(redacted_name)
                renamed_symbols[import_name] = redacted_name

    lib.seek(string_pool_start)
    string_pool = lib.read(string_pool_end - string_pool_start)
    lib.seek(string_pool_start)
    lib.write(update_string_pool(string_pool, renamed_symbols))


def read_image_archive_member_header(f):
    data = f.read(60)

    raw_name = data[:16].decode("utf-8")
    name = raw_name[:raw_name.index("/")]

    size = int(data[48:58].decode("utf-8"))

    return ImageArchiveMemberHeader(name, size, data)


def read_import_object_header(f):
    data = f.read(20)

    (sig1, sig2, version, machine, time_date_stamp, size_of_data) \
            = struct.unpack("<HHHHII", data[:16])

    return ImportObjectHeader(sig1, sig2, version, machine, size_of_data, data)


def update_string_pool(pool, renames):
    return b"\x00".join(map(lambda s: renames.get(s, s), pool.split(b"\x00")))


@dataclass
class ImageArchiveMemberHeader:
    name: str
    size: int
    raw_header: bytes


@dataclass
class ImportObjectHeader:
    sig1: int
    sig2: int
    version: int
    machine: int
    size_of_data: int
    raw_header: bytes


if __name__ == "__main__":
    main(sys.argv)
