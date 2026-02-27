import requests
r = requests.get(
    "https://api.github.com/repos/rezars19/rz-automedata/releases/tags/v1.2.0",
    headers={"Accept": "application/vnd.github.v3+json"},
    timeout=10
)
data = r.json()
tag = data.get("tag_name", "N/A")
assets = data.get("assets", [])
print(f"Release: {tag}")
print(f"Assets uploaded: {len(assets)}")
for a in assets:
    size_mb = a["size"] / 1024 / 1024
    print(f"  - {a['name']} ({size_mb:.1f} MB)")
    print(f"    URL: {a['browser_download_url']}")
if not assets:
    print("\n⚠️  No .exe uploaded to this release!")
    print("You need to upload RZAutomedata.exe as a release asset.")
