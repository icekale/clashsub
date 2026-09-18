PROXY_NAME = "Tailscale"
SECRET_NAME = "tailscale_auth_key"


def inject_tailscale(document: dict, auth_key: str, exit_node: str = "") -> None:
    """把 Tailscale 出站追加到 Clash 文档。

    exit-node 必填：没有它，这个出站只进 tailnet、到不了公网，选中就会断网。
    """
    key = (auth_key or "").strip()
    node = (exit_node or "").strip()
    if not key or not node or not isinstance(document, dict):
        return
    proxy = {
        "name": PROXY_NAME,
        "type": "tailscale",
        "auth-key": key,
        "exit-node": node,
        "exit-node-allow-lan-access": True,
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
