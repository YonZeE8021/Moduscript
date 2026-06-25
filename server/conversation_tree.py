"""Conversation tree model for session branching."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

NODE_USER = "user"
NODE_ASSISTANT = "assistant"
KIND_INITIAL = "initial"
KIND_FOLLOW_UP = "follow_up"


def _new_node_id(prefix: str = "node") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def empty_tree() -> dict[str, Any]:
    return {"root_id": None, "active_path": [], "nodes": {}}


def _node(
    *,
    node_type: str,
    parent_id: str | None,
    **extra: Any,
) -> dict[str, Any]:
    node_id = extra.pop("id", None) or _new_node_id()
    return {
        "id": node_id,
        "type": node_type,
        "parent_id": parent_id,
        "children": [],
        "git_ref": extra.pop("git_ref", None),
        "agent_cli_session_id": extra.pop("agent_cli_session_id", None),
        **extra,
    }


def migrate_linear_to_tree(
    *,
    final_prompt: str,
    readable_blueprint: str,
    turns: list[dict[str, Any]],
    user_messages: list[dict[str, Any]],
    git_ref: str | None = None,
) -> dict[str, Any]:
    follow_ups = [m for m in user_messages if m.get("kind") == "follow_up"]
    nodes: dict[str, Any] = {}
    active_path: list[str] = []

    root = _node(
        node_type=NODE_USER,
        parent_id=None,
        kind=KIND_INITIAL,
        content=final_prompt,
        readable_blueprint=readable_blueprint,
        git_ref=git_ref,
    )
    nodes[root["id"]] = root
    active_path.append(root["id"])
    parent_id = root["id"]

    for index, turn in enumerate(turns):
        assistant = _node(
            node_type=NODE_ASSISTANT,
            parent_id=parent_id,
            turn_id=turn.get("id") or f"interaction-{index + 1}",
            turn_snapshot=deepcopy(turn),
        )
        nodes[assistant["id"]] = assistant
        nodes[parent_id]["children"].append(assistant["id"])
        active_path.append(assistant["id"])
        parent_id = assistant["id"]

        if index < len(follow_ups):
            msg = follow_ups[index]
            user = _node(
                node_type=NODE_USER,
                parent_id=parent_id,
                kind=KIND_FOLLOW_UP,
                content=msg.get("content") or "",
                message_id=msg.get("id"),
                sent_by_admin=bool(msg.get("sent_by_admin")),
                admin_actor=msg.get("admin_actor"),
                admin_actor_id=msg.get("admin_actor_id"),
            )
            nodes[user["id"]] = user
            nodes[parent_id]["children"].append(user["id"])
            active_path.append(user["id"])
            parent_id = user["id"]

    return {
        "root_id": root["id"],
        "active_path": active_path,
        "root_sibling_ids": [root["id"]],
        "nodes": nodes,
    }


def ancestry_path(tree: dict[str, Any], node_id: str) -> list[str]:
    node = get_node(tree, node_id)
    if not node:
        raise ValueError("node not found")
    path: list[str] = []
    current: dict[str, Any] | None = node
    while current:
        path.insert(0, current["id"])
        pid = current.get("parent_id")
        current = get_node(tree, pid) if pid else None
    return path


def commit_active_path(tree: dict[str, Any], path: list[str]) -> None:
    tree["active_path"] = list(path)
    nodes = tree.get("nodes") or {}
    for i in range(len(path) - 1):
        parent = nodes.get(path[i])
        child_id = path[i + 1]
        if parent is not None:
            parent["active_child_id"] = child_id


def backfill_active_children(tree: dict[str, Any]) -> None:
    path = tree.get("active_path") or []
    if not path:
        return
    nodes = tree.get("nodes") or {}
    needs_backfill = any(
        i < len(path) - 1
        and not (nodes.get(path[i]) or {}).get("active_child_id")
        for i in range(len(path) - 1)
    )
    if needs_backfill:
        commit_active_path(tree, path)


def ensure_tree(record: Any) -> dict[str, Any]:
    tree = getattr(record, "conversation_tree", None)
    if tree and tree.get("nodes"):
        backfill_active_children(tree)
        return tree
    tree = migrate_linear_to_tree(
        final_prompt=record.final_prompt,
        readable_blueprint=record.readable_blueprint,
        turns=record.turns or [],
        user_messages=record.user_messages or [],
        git_ref=getattr(record, "workspace_git_ref", None),
    )
    commit_active_path(tree, tree.get("active_path") or [])
    record.conversation_tree = tree
    return tree


def get_node(tree: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    return (tree.get("nodes") or {}).get(node_id)


def sync_tree_to_linear(record: Any) -> None:
    tree = ensure_tree(record)
    nodes = tree.get("nodes") or {}
    path = tree.get("active_path") or []
    if not path:
        return

    turns: list[dict[str, Any]] = []
    user_messages: list[dict[str, Any]] = []
    final_prompt = record.final_prompt
    readable_blueprint = record.readable_blueprint
    agent_cli_session_id = None

    for node_id in path:
        node = nodes.get(node_id)
        if not node:
            continue
        if node.get("type") == NODE_USER:
            if node.get("kind") == KIND_INITIAL:
                final_prompt = node.get("content") or final_prompt
                readable_blueprint = node.get("readable_blueprint") or readable_blueprint
            elif node.get("kind") == KIND_FOLLOW_UP:
                user_messages.append(
                    {
                        "id": node.get("message_id") or node_id,
                        "role": "user",
                        "content": node.get("content") or "",
                        "kind": KIND_FOLLOW_UP,
                        "created_at": node.get("created_at"),
                        **(
                            {"sent_by_admin": True, "admin_actor": node.get("admin_actor")}
                            if node.get("sent_by_admin")
                            else {}
                        ),
                    }
                )
        elif node.get("type") == NODE_ASSISTANT:
            snap = node.get("turn_snapshot")
            if snap:
                turns.append(deepcopy(snap))
            if node.get("agent_cli_session_id"):
                agent_cli_session_id = node["agent_cli_session_id"]

    record.final_prompt = final_prompt
    record.readable_blueprint = readable_blueprint
    record.turns = turns
    record.user_messages = user_messages
    record.agent_cli_session_id = agent_cli_session_id


def git_ref_for_path_leaf(tree: dict[str, Any]) -> str | None:
    nodes = tree.get("nodes") or {}
    for node_id in reversed(tree.get("active_path") or []):
        node = nodes.get(node_id)
        if node and node.get("git_ref"):
            return node["git_ref"]
    return None


def git_ref_for_reset_before(tree: dict[str, Any], node_id: str) -> str | None:
    node = get_node(tree, node_id)
    if not node:
        return None
    if node.get("type") == "assistant":
        start_ref = node.get("git_ref_start")
        if start_ref:
            return start_ref
    parent = parent_node(tree, node_id)
    if parent and parent.get("git_ref"):
        return parent["git_ref"]
    for nid in reversed(ancestry_path(tree, node_id)[:-1]):
        n = get_node(tree, nid)
        if n and n.get("git_ref"):
            return n["git_ref"]
    return None


def set_node_git_refs(
    tree: dict[str, Any],
    node_id: str,
    *,
    git_ref: str | None = None,
    git_ref_start: str | None = None,
) -> None:
    node = get_node(tree, node_id)
    if not node:
        return
    if git_ref is not None:
        node["git_ref"] = git_ref
    if git_ref_start is not None:
        node["git_ref_start"] = git_ref_start


def backfill_git_refs_from_workspace(tree: dict[str, Any], workspace_head: str | None) -> bool:
    if not workspace_head:
        return False
    path = tree.get("active_path") or []
    nodes = tree.get("nodes") or {}
    changed = False
    for node_id in path:
        node = nodes.get(node_id)
        if node and not node.get("git_ref"):
            node["git_ref"] = workspace_head
            changed = True
    return changed


def git_ref_before_node(tree: dict[str, Any], node_id: str) -> str | None:
    return git_ref_for_reset_before(tree, node_id)


def active_path_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = tree.get("nodes") or {}
    return [nodes[nid] for nid in (tree.get("active_path") or []) if nid in nodes]


def last_assistant_on_path(tree: dict[str, Any]) -> dict[str, Any] | None:
    for node in reversed(active_path_nodes(tree)):
        if node.get("type") == NODE_ASSISTANT:
            return node
    return None


def last_user_on_path(tree: dict[str, Any]) -> dict[str, Any] | None:
    for node in reversed(active_path_nodes(tree)):
        if node.get("type") == NODE_USER:
            return node
    return None


def parent_node(tree: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    node = get_node(tree, node_id)
    if not node:
        return None
    parent_id = node.get("parent_id")
    if not parent_id:
        return None
    return get_node(tree, parent_id)


def sibling_index(tree: dict[str, Any], node_id: str) -> tuple[int, int]:
    node = get_node(tree, node_id)
    if not node:
        return 0, 0
    parent_id = node.get("parent_id")
    if not parent_id:
        siblings = list(tree.get("root_sibling_ids") or [])
        if not siblings and tree.get("root_id"):
            siblings = [tree["root_id"]]
        if node_id not in siblings:
            siblings = siblings + [node_id]
    else:
        parent = get_node(tree, parent_id)
        siblings = list((parent or {}).get("children") or [])
        if node_id not in siblings:
            siblings = siblings + [node_id]
    return siblings.index(node_id), len(siblings)


def branch_meta_for_node(tree: dict[str, Any], node_id: str) -> dict[str, Any]:
    index, total = sibling_index(tree, node_id)
    node = get_node(tree, node_id)
    parent_id = (node or {}).get("parent_id")
    if parent_id:
        parent = get_node(tree, parent_id)
        siblings = (parent or {}).get("children") or []
    else:
        siblings = list(tree.get("root_sibling_ids") or [])
        if not siblings and tree.get("root_id"):
            siblings = [tree["root_id"]]
    return {
        "node_id": node_id,
        "branch_index": index,
        "branch_total": total,
        "sibling_ids": siblings,
        "has_prev": index > 0,
        "has_next": index < total - 1,
    }


def branch_nav_for_path(tree: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for node_id in tree.get("active_path") or []:
        node = get_node(tree, node_id)
        if not node:
            continue
        meta = branch_meta_for_node(tree, node_id)
        if meta["branch_total"] > 1 or node.get("type") in {NODE_USER, NODE_ASSISTANT}:
            result.append({**meta, "type": node.get("type"), "kind": node.get("kind")})
    return result


def set_node_git_ref(tree: dict[str, Any], node_id: str, git_ref: str | None) -> None:
    node = get_node(tree, node_id)
    if node is not None:
        node["git_ref"] = git_ref


def update_assistant_turn_snapshot(
    tree: dict[str, Any], node_id: str, turn: dict[str, Any], *, git_ref: str | None = None
) -> None:
    node = get_node(tree, node_id)
    if not node or node.get("type") != NODE_ASSISTANT:
        return
    node["turn_snapshot"] = deepcopy(turn)
    if git_ref:
        node["git_ref"] = git_ref


def fork_assistant_sibling(
    tree: dict[str, Any],
    assistant_node_id: str,
    *,
    turn_id: str,
    git_ref: str | None = None,
) -> dict[str, Any]:
    old = get_node(tree, assistant_node_id)
    if not old or old.get("type") != NODE_ASSISTANT:
        raise ValueError("assistant node not found")
    parent_id = old.get("parent_id")
    new_assistant = _node(
        node_type=NODE_ASSISTANT,
        parent_id=parent_id,
        turn_id=turn_id,
        turn_snapshot={
            "id": turn_id,
            "summary": "",
            "thinking": [],
            "tools": [],
            "pending_action": None,
            "user_reply": None,
            "progress": None,
        },
        git_ref=git_ref,
    )
    nodes = tree.setdefault("nodes", {})
    nodes[new_assistant["id"]] = new_assistant
    if parent_id:
        parent = nodes.get(parent_id)
        if parent is not None:
            parent.setdefault("children", []).append(new_assistant["id"])
    _replace_path_suffix(tree, assistant_node_id, new_assistant["id"])
    commit_active_path(tree, tree.get("active_path") or [])
    return new_assistant


def fork_user_sibling(
    tree: dict[str, Any],
    user_node_id: str,
    *,
    content: str,
    kind: str | None = None,
    readable_blueprint: str | None = None,
) -> dict[str, Any]:
    old = get_node(tree, user_node_id)
    if not old or old.get("type") != NODE_USER:
        raise ValueError("user node not found")
    parent_id = old.get("parent_id")
    new_user = _node(
        node_type=NODE_USER,
        parent_id=parent_id,
        kind=kind or old.get("kind") or KIND_FOLLOW_UP,
        content=content,
        readable_blueprint=readable_blueprint if readable_blueprint is not None else old.get("readable_blueprint"),
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
    )
    nodes = tree.setdefault("nodes", {})
    nodes[new_user["id"]] = new_user
    if parent_id:
        parent = nodes.get(parent_id)
        if parent is not None:
            parent.setdefault("children", []).append(new_user["id"])
    else:
        versions = tree.setdefault("root_sibling_ids", [])
        if user_node_id not in versions:
            versions.append(user_node_id)
        if new_user["id"] not in versions:
            versions.append(new_user["id"])
        tree["root_id"] = new_user["id"]
    _replace_path_suffix(tree, user_node_id, new_user["id"])
    commit_active_path(tree, tree.get("active_path") or [])
    return new_user


def append_user_follow_up(tree: dict[str, Any], content: str, *, message_id: str | None = None) -> dict[str, Any]:
    path = tree.get("active_path") or []
    parent_id = path[-1] if path else None
    new_user = _node(
        node_type=NODE_USER,
        parent_id=parent_id,
        kind=KIND_FOLLOW_UP,
        content=content,
        message_id=message_id or f"msg-{uuid.uuid4().hex[:8]}",
    )
    nodes = tree.setdefault("nodes", {})
    nodes[new_user["id"]] = new_user
    if parent_id:
        parent = nodes.get(parent_id)
        if parent is not None:
            parent.setdefault("children", []).append(new_user["id"])
    path.append(new_user["id"])
    commit_active_path(tree, path)
    return new_user


def append_assistant_turn(tree: dict[str, Any], turn_id: str) -> dict[str, Any]:
    path = tree.get("active_path") or []
    parent_id = path[-1] if path else None
    assistant = _node(
        node_type=NODE_ASSISTANT,
        parent_id=parent_id,
        turn_id=turn_id,
        turn_snapshot={
            "id": turn_id,
            "summary": "",
            "thinking": [],
            "tools": [],
            "pending_action": None,
            "user_reply": None,
            "progress": None,
        },
    )
    nodes = tree.setdefault("nodes", {})
    nodes[assistant["id"]] = assistant
    if parent_id:
        parent = nodes.get(parent_id)
        if parent is not None:
            parent.setdefault("children", []).append(assistant["id"])
    path.append(assistant["id"])
    commit_active_path(tree, path)
    return assistant


def switch_branch(tree: dict[str, Any], node_id: str) -> None:
    path = ancestry_path(tree, node_id)
    current_id = node_id
    nodes = tree.get("nodes") or {}
    while True:
        node = nodes.get(current_id)
        if not node:
            break
        child_id = node.get("active_child_id")
        children = node.get("children") or []
        if child_id and child_id in children:
            path.append(child_id)
            current_id = child_id
        else:
            break
    commit_active_path(tree, path)


def _replace_path_suffix(tree: dict[str, Any], old_node_id: str, new_node_id: str) -> None:
    path = tree.get("active_path") or []
    if old_node_id in path:
        idx = path.index(old_node_id)
        path = path[:idx] + [new_node_id]
    else:
        path = (path or []) + [new_node_id]
    commit_active_path(tree, path)


def truncate_path_to(tree: dict[str, Any], node_id: str, *, inclusive: bool = True) -> None:
    path = tree.get("active_path") or []
    if node_id not in path:
        raise ValueError("node not on active path")
    idx = path.index(node_id)
    commit_active_path(tree, path[: idx + 1] if inclusive else path[:idx])


def path_prompt_for_rerun(tree: dict[str, Any]) -> tuple[str, str]:
    """Return (kind, prompt) where kind is 'initial' or 'follow_up'."""
    nodes = active_path_nodes(tree)
    users = [n for n in nodes if n.get("type") == NODE_USER]
    if not users:
        return "initial", ""
    last_user = users[-1]
    if last_user.get("kind") == KIND_INITIAL:
        return "initial", last_user.get("content") or ""
    return "follow_up", last_user.get("content") or ""


def tree_to_snapshot_extra(tree: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_tree": deepcopy(tree),
        "branch_nav": branch_nav_for_path(tree),
    }
