project('frida-node', 'cpp',
  version: '0.0.0',
)

node = find_program('node')
npm = find_program('npm')
python = import('python').find_installation()

version = run_command(python, files('scripts' / 'detect-version.py'),
  files('package.json'),
  capture: true,
  check: true,
).stdout().strip()

runtime = get_option('runtime')
target = get_option('target')

system = host_machine.system()

node_platforms = {
  'windows': 'win32',
}
node_platform = node_platforms.get(system, system)

gyp_oses = {
  'windows': 'win',
  'darwin': 'mac',
}
gyp_os = gyp_oses.get(system, system)

gyp_archs = {
  'x86': 'ia32',
  'x86_64': 'x64',
  'arm': 'armv7l',
  'aarch64': 'arm64',
}
cpu_family = host_machine.cpu_family()
gyp_arch = gyp_archs.get(cpu_family, cpu_family)

fs = import('fs')
if runtime == 'node-webkit'
  host_binary = 'node'
  if host_machine.system() == 'windows'
    host_binary += '.dll'
  endif
else
  host_binary = 'node'
  if host_machine.system() == 'windows'
    host_binary += '.exe'
  endif
endif

message('Fetching ABI bits')
metadata = run_command(python, files('scripts' / 'fetch-abi-bits.py'),
  runtime, target, gyp_os, gyp_arch,
  node, npm, meson.current_build_dir(),
  capture: true,
  check: true,
).stdout().strip().split('\n')
abi = 0
node_defines = []
node_incdirs = []
node_libs = []
foreach item : metadata
  if item.startswith('abi: ')
    abi = item.substring(5)
  elif item.startswith('node_defines: ')
    node_defines += item.substring(14).split(' ')
  elif item.startswith('node_incdir: ')
    node_incdirs += item.substring(13)
  elif item.startswith('node_lib: ')
    node_libs += meson.current_build_dir() / item.substring(10)
  else
    error('Unexpected API bits item:', item)
  endif
endforeach

extra_cpp_args = []
extra_link_args = []

foreach d : node_defines
  extra_cpp_args += '-D' + d
endforeach

if host_machine.system() == 'windows'
  extra_cpp_args += [
    '/Zc:__cplusplus',
    '/Zc:inline',
    '/Zc:threadSafeInit-',
    '/wd4244',
    '/wd4251',
    '/wd4275',
    '/wd5051',
    '-DNOMINMAX',
  ]
  extra_link_args += [
    '/DELAYLOAD:' + host_binary,
    '-lDelayImp',
  ]
endif

nan_dep = dependency('nan')

frida_core_dep = dependency('frida-core-1.0', default_options: [
  'compiler_backend=disabled',
])

subdir('src')

binding = shared_module('frida_binding', frida_binding_sources,
  name_prefix: '',
  name_suffix: 'node',
  include_directories: include_directories(node_incdirs),
  implicit_include_directories: false,
  override_options: [
    'cpp_std=c++17',
    'cpp_eh=none',
    'cpp_rtti=false',
  ],
  cpp_args: [
    '-DBUILDING_NODE_EXTENSION',
    '-DNODE_GYP_MODULE_NAME=frida_binding',
    f'-DHOST_BINARY="@host_binary@"',
    '-DFRIDA_' + runtime.underscorify().to_upper(),
  ] + extra_cpp_args,
  link_args: node_libs + extra_link_args,
  dependencies: [frida_core_dep, nan_dep],
)

custom_target('prebuild',
  input: [binding],
  output: [f'frida-v@version@-@runtime@-v@abi@-@node_platform@-@gyp_arch@.tar.gz'],
  command: [
    python,
    files('scripts' / 'package.py'),
    '@INPUT@',
    '@OUTPUT@',
  ],
)

package_json = files('package.json')
package_lock_json = files('package-lock.json')
tsconfig_json = files('tsconfig.json')

install_data(package_json,
  install_dir: get_option('libdir') / 'node_modules' / 'frida',
)

subdir('lib')