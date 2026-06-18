import json

import requests

BASE = 'https://lscluster.hockeytech.com/feed/index.php'
KEY = '446521baf8c38984'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.thepwhl.com/'}

r = requests.get(BASE, params={
    'feed': 'statviewfeed',
    'view': 'gameCenterPlayByPlay',
    'game_id': '213',
    'key': KEY,
    'client_code': 'pwhl',
    'lang': 'en',
    'league_id': '',
}, headers=HEADERS, timeout=20)

text = r.text.strip()
if '(' in text:
    text = text[text.index('(')+1:text.rindex(')')]
data = json.loads(text)

# Show first blocked shot and first goal event
for event_type in ('blocked_shot', 'goal'):
    for e in data:
        if isinstance(e, dict) and e.get('event') == event_type:
            print(f'First {event_type}:')
            print(json.dumps(e, indent=2))
            print()
            break
