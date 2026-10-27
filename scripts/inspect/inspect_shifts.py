import requests

HEADERS = {"User-Agent": "EyeWall-Analytics/1.0"}
r = requests.get(
    "https://www.nhl.com/scores/htmlreports/20252026/TV020373.HTM", headers=HEADERS, timeout=20
)
text = r.text
# Find by player ID we know is in this game (Giroux)
idx = text.find("8473512")
if idx == -1:
    print("player ID not found, trying shift number")
    idx = text.find("Shift #")
    if idx == -1:
        idx = text.find("Shift")
print(f"Found at index {idx}")
print(text[idx : idx + 3000])
