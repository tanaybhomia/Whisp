python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').path())
conf.set('pkgdatadir', join_paths(get_option('prefix'), get_option('datadir'), meson.project_name()))
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))

configure_file(
  input: 'whisp.in',
  output: 'whisp',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

whisp_sources = [
  'whisp/__init__.py',
  'whisp/config.py',
  'whisp/highlighter.py',
  'whisp/editor.py',
  'whisp/window.py',
  'whisp/main.py',
  'whisp/text_search.py',
  'whisp/notes.py',
  'whisp/search_provider.py',
  'whisp/stats.py',
]

pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
moduledir = join_paths(pkgdatadir, 'whisp')

install_data(whisp_sources, install_dir: moduledir)
