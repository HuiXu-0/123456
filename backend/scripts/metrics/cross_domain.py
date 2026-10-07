# -*- coding: utf-8 -*-
"""B11 跨领域验证：第二领域「计算机网络」最小集，走通画像→生成→三指标全链路。

证明方法论领域无关：换一个与 AI 无关的领域（6 个网络知识点 + 5 份领域文档 +
概念清单），沿用完全相同的 RAG→生成→审核管线与三指标口径，看能否同样达标。

自包含 & 不污染：脚本临时种入 net_* 知识点与领域文档、生成讲义并测指标，
**结束时清理** net_* 知识点/掌握度/讲义缓存/工作流轨迹与领域文档（恢复 6 AI 知识点
基线），从而不影响 AI 指标脚本与契约测试（它们假设恰好 6 个核心知识点）。

运行（backend 目录，deepseek 真实生成）：
    python scripts/metrics/cross_domain.py --out scripts/metrics/_out/cross_domain.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from difficulty_adaptation import difficulty_score  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.tasks import Task  # noqa: E402
from app.models.entities import (  # noqa: E402
    KnowledgeDocument, KnowledgePoint, Mastery, ResourceCache, User, WorkflowTrace,
)
from app.rag.grounding import sentence_grounding  # noqa: E402
from app.services import kb as kb_service  # noqa: E402
from app.services.resource import _retrieve_for_lecture, generate_lecture  # noqa: E402

DIFFICULTIES = ["入门", "初级", "高级"]
NET_DIR = Path(__file__).resolve().parents[2] / "seed_docs" / "network"

# 6 网络知识点：(id, name, lessonSeq, desc)
NET_KPS = [
    ("net_layer", "网络分层模型", 101, "网络分层思想、OSI 七层与 TCP/IP 模型、封装与解封装。"),
    ("net_physical", "物理层与数据链路层", 102, "物理层信号与介质、数据链路层、MAC 与以太网、差错检测。"),
    ("net_ip", "网络层与IP路由", 103, "IP 地址与子网、路由与转发、路由协议、ARP/ICMP/NAT。"),
    ("net_transport", "传输层TCP与UDP", 104, "端口与进程、TCP 可靠传输与三次握手、流量与拥塞控制、UDP。"),
    ("net_app", "应用层HTTP与DNS", 105, "HTTP 请求响应与方法状态码、无状态与 Cookie、DNS 域名解析。"),
    ("net_security", "网络安全基础", 106, "机密性完整性可用性、对称与非对称加密、TLS/HTTPS、常见威胁与防护。"),
]

# 核心概念清单（每 KP 6 项，与 AI 域同口径：任一同义表述命中即算覆盖）
NET_CONCEPTS = {
    "net_layer": [
        ("分层思想", ["分层", "层次"]), ("OSI模型", ["OSI", "七层"]),
        ("TCP/IP模型", ["TCP/IP", "四层"]), ("封装", ["封装", "首部", "解封装"]),
        ("协议数据单元", ["协议数据单元", "PDU", "帧", "分组", "报文段"]),
        ("服务与接口", ["服务", "接口", "对等层"]),
    ],
    "net_physical": [
        ("物理层", ["物理层", "比特", "信号"]), ("传输介质", ["双绞线", "光纤", "无线", "介质"]),
        ("数据链路层", ["数据链路", "帧"]), ("MAC地址", ["MAC", "物理地址"]),
        ("以太网", ["以太网", "交换机"]), ("差错检测", ["CRC", "校验", "差错"]),
    ],
    "net_ip": [
        ("IP地址", ["IP 地址", "IP地址", "IPv4", "IPv6"]),
        ("子网", ["子网", "掩码", "CIDR", "网络号"]), ("路由", ["路由", "下一跳", "路由表"]),
        ("转发", ["转发", "路由器"]), ("路由协议", ["OSPF", "BGP", "路由协议"]),
        ("配套协议", ["ARP", "ICMP", "NAT", "ping"]),
    ],
    "net_transport": [
        ("端口", ["端口", "进程"]), ("TCP", ["TCP", "面向连接"]),
        ("三次握手", ["三次握手", "握手", "挥手"]),
        ("可靠传输", ["可靠", "确认", "重传", "序号"]),
        ("流量与拥塞控制", ["流量控制", "拥塞", "滑动窗口", "慢启动"]),
        ("UDP", ["UDP", "无连接", "数据报"]),
    ],
    "net_app": [
        ("HTTP", ["HTTP", "请求", "响应"]),
        ("方法与状态码", ["GET", "POST", "状态码", "404", "200"]),
        ("无状态与会话", ["无状态", "Cookie", "会话"]), ("DNS", ["DNS", "域名", "解析"]),
        ("DNS层次", ["根域名", "权威", "递归", "顶级域"]),
        ("HTTP演进", ["HTTP/2", "HTTP/3", "HTTPS"]),
    ],
    "net_security": [
        ("安全三要素", ["机密性", "完整性", "可用性"]), ("对称加密", ["对称加密"]),
        ("非对称加密", ["非对称", "公钥", "私钥"]), ("TLS/HTTPS", ["TLS", "HTTPS", "证书"]),
        ("数字签名", ["数字签名", "签名"]),
        ("常见威胁", ["中间人", "DDoS", "注入", "跨站", "防火墙"]),
    ],
}


def _seed_net_kps(db) -> None:
    for kp_id, name, seq, desc in NET_KPS:
        if db.get(KnowledgePoint, kp_id) is None:
            db.add(KnowledgePoint(id=kp_id, name=name, lesson_seq=seq,
                                  graph_node_id=kp_id, description=desc))
    db.commit()


def _ingest_net_docs(db) -> list[str]:
    """灌入 seed_docs/network/ 下领域文档（幂等），返回本次涉及的文档 id。"""
    doc_specs: list[tuple[str, str, bytes]] = []
    doc_ids: list[str] = []
    files = sorted(f for f in os.listdir(NET_DIR) if f.lower().endswith((".md", ".txt")))
    for filename in files:
        exists = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.filename == filename).first()
        if exists is not None:
            doc_ids.append(exists.id)
            continue
        raw = (NET_DIR / filename).read_bytes()
        doc_id = kb_service._next_doc_id(db)
        db.add(KnowledgeDocument(id=doc_id, title=kb_service._derive_title(filename),
                                 filename=filename, size=len(raw), category="教材",
                                 status="pending", chunks=0))
        db.flush()
        doc_specs.append((doc_id, filename, raw))
        doc_ids.append(doc_id)
    db.commit()
    if doc_specs:
        kb_service._do_ingest(doc_specs, Task(task_id="cross_domain"))
    return doc_ids


def _cleanup(db, doc_ids: list[str], user_id: str) -> None:
    net_ids = [k[0] for k in NET_KPS]
    for doc_id in doc_ids:
        try:
            kb_service.delete_document(db, doc_id)  # 移除向量切片 + 文档行
        except Exception:  # noqa: BLE001
            pass
    db.query(ResourceCache).filter(ResourceCache.kp_id.in_(net_ids)).delete(synchronize_session=False)
    db.query(Mastery).filter(Mastery.user_id == user_id, Mastery.kp_id.in_(net_ids)).delete(synchronize_session=False)
    db.query(WorkflowTrace).filter(WorkflowTrace.kp_id.in_(net_ids)).delete(synchronize_session=False)
    db.query(KnowledgePoint).filter(KnowledgePoint.id.in_(net_ids)).delete(synchronize_session=False)
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    measured_at = datetime.now(timezone.utc).isoformat()

    db = SessionLocal()
    doc_ids: list[str] = []
    user_id = "u_10001"
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        user_id = user.id
        _seed_net_kps(db)
        doc_ids = _ingest_net_docs(db)
        print(f"[cross] 网络领域：{len(NET_KPS)} 知识点 + {len(doc_ids)} 文档，生成 18 份讲义 …")

        lectures: dict[tuple[str, str], str] = {}
        for kp_id, name, _seq, _desc in NET_KPS:
            for difficulty in DIFFICULTIES:
                print(f"  [讲义] {kp_id} × {difficulty} …", flush=True)
                payload = generate_lecture(db, user.id, kp_id, difficulty)
                lectures[(kp_id, difficulty)] = payload["markdown"]

        names = {k[0]: k[1] for k in NET_KPS}
        # ① 幻觉率（逐句接地，口径同 B8）
        h_details, h_sum = [], 0.0
        for (kp_id, difficulty), md in sorted(lectures.items()):
            ctx = [c["content"] for c in _retrieve_for_lecture(kp_id, names[kp_id], difficulty)]
            g = sentence_grounding(md, ctx)
            h_details.append({"kpId": kp_id, "difficulty": difficulty,
                              "rate": g["hallucinationRate"], "totalSentences": g["totalSentences"]})
            h_sum += g["hallucinationRate"]
        h_rate = round(h_sum / len(h_details), 4)

        # ② 难度适配率（难度分有序对，口径同 B8）
        rank = {d: i for i, d in enumerate(DIFFICULTIES)}
        a_details, correct, total = [], 0, 0
        for kp_id, *_ in NET_KPS:
            feats = {d: difficulty_score(lectures[(kp_id, d)]) for d in DIFFICULTIES}
            bad = []
            for low, high in combinations(DIFFICULTIES, 2):
                lo, hi = (low, high) if rank[low] < rank[high] else (high, low)
                ok = feats[lo]["score"] < feats[hi]["score"]
                correct += int(ok); total += 1
                if not ok:
                    bad.append(f"{lo}<{hi}")
            a_details.append({"kpId": kp_id, "scores": {d: feats[d]["score"] for d in DIFFICULTIES}, "badPairs": bad})
        a_rate = round(correct / total, 4)

        # ③ 知识覆盖率（网络概念清单命中，口径同 B8）
        c_details, hit_total, concept_total = [], 0, 0
        for kp_id, checklist in NET_CONCEPTS.items():
            combined = "\n".join(lectures.get((kp_id, d), "") for d in DIFFICULTIES).lower()
            hits = [c for c, al in checklist if any(a.lower() in combined for a in al)]
            misses = [c for c, al in checklist if not any(a.lower() in combined for a in al)]
            hit_total += len(hits); concept_total += len(checklist)
            c_details.append({"kpId": kp_id, "hit": len(hits), "total": len(checklist), "misses": misses})
        c_rate = round(hit_total / concept_total, 4)

        payload = {
            "domain": "计算机网络", "measuredAt": measured_at,
            "provider": settings.llm_provider, "threshold": settings.grounding_threshold,
            "kpCount": len(NET_KPS), "docCount": len(doc_ids), "sampleSize": len(lectures),
            "rates": {"hallucinationRate": h_rate, "adaptationRate": a_rate, "coverageRate": c_rate},
            "hallucinationDetails": h_details, "adaptationDetails": a_details, "coverageDetails": c_details,
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[cross] 写入 {out}")
        print(f"[cross] 网络领域：幻觉率={h_rate} 适配率={a_rate} 覆盖率={c_rate}")
    finally:
        _cleanup(db, doc_ids, user_id)
        db.close()
        print("[cross] 已清理 net_* 知识点/掌握度/缓存/轨迹与领域文档（恢复 6 AI 基线）")


if __name__ == "__main__":
    main()
