PROXY_NAME = "Tailscale"
SECRET_NAME = "tailscale_auth_key"


def inject_tailscale(document: dict, auth_key: str) -> None:
    key = (auth_key or "").strip()
    if not key or not isinstance(document, dict):
        return
    proxy = {
        "name": PROXY_NAME,
        "type": "tailscale",
        "auth-key": key,
        "udp": True,
    }
    proxies = document.get("proxies")
    if not isinstance(proxies, list):
        document["proxies"] = [proxy]
    else:
        document["proxies"] = [
            item
            for item in proxies
            if not (isinstance(item, dict) and item.get("name") == PROXY_NAME)
        ]
        document["proxies"].append(proxy)
    groups = document.get("proxy-groups")
    if not isinstance(groups, list):
        return
    for group in groups:
        members = group.get("proxies") if isinstance(group, dict) else None
        if isinstance(members, list) and PROXY_NAME not in members:
            members.append(PROXY_NAME)
