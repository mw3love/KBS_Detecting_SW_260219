import os

dirs = [
    'kbs_monitor/config',
    'kbs_monitor/ui',
    'kbs_monitor/core',
    'kbs_monitor/utils',
    'kbs_monitor/resources/sounds',
    'kbs_monitor/resources/styles',
    '.claude',
]

for d in dirs:
    os.makedirs(d, exist_ok=True)
    print(f'Created: {d}')

print('All directories created successfully.')
