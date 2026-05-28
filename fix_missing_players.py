from db import get_client, upsert

missing = [
    {'id': 8480291, 'name': "Skyler Brind'Amour", 'position': 'C'},
    {'id': 8481004, 'name': 'Josiah Slavin',       'position': 'L'},
    {'id': 8481491, 'name': 'Noah Philp',           'position': 'C'},
    {'id': 8481562, 'name': 'Domenick Fensore',     'position': 'D'},
    {'id': 8482187, 'name': 'Ronan Seeley',         'position': 'D'},
    {'id': 8482785, 'name': 'Justin Robidas',       'position': 'C'},
    {'id': 8482911, 'name': 'Joel Nystrom',         'position': 'D'},
    {'id': 8484203, 'name': 'Bradly Nadeau',        'position': 'L'},
    {'id': 8484392, 'name': 'Felix Unger Sorum',    'position': 'R'},
]

client = get_client()
upsert(client, 'players', missing, 'id')
print('Done')
