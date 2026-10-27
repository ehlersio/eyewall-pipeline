import requests

HEADERS = {"User-Agent": "EyeWall-Analytics/1.0"}
r = requests.get(
    "https://www.nhl.com/scores/htmlreports/20252026/TV020373.HTM", headers=HEADERS, timeout=20
)
text = r.text
idx = text.find("Shift #")
print(text[idx - 2000 : idx + 200])
