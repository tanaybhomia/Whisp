python = import('python').find_installation('python3')

test('Test text_search.py',
  python,
  args: [join_paths(meson.current_source_dir(), 'test_text_search.py')],
  env: ['PYTHONPATH=' + join_paths(meson.project_source_root(), 'src')]
)

test('Test notes.py',
  python,
  args: [join_paths(meson.current_source_dir(), 'test_notes.py')],
  env: ['PYTHONPATH=' + join_paths(meson.project_source_root(), 'src')]
)

test('Test highlighter.py',
  python,
  args: [join_paths(meson.current_source_dir(), 'test_highlighter.py')],
  env: ['PYTHONPATH=' + join_paths(meson.project_source_root(), 'src')]
)

test('Test stats.py',
  python,
  args: [join_paths(meson.current_source_dir(), 'test_stats.py')],
  env: ['PYTHONPATH=' + join_paths(meson.project_source_root(), 'src')]
)
