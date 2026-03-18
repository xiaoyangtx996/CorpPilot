"""
董事会会议室。
封装董事会提案、讨论、投票与计票流程。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import BoardRoom, DecisionType, VoteResult  # noqa: E402


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="CorpPilot 董事会会议室")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    create_parser = subparsers.add_parser("create", help="创建提案")
    create_parser.add_argument("title", help="提案标题")
    create_parser.add_argument("content", help="提案内容")
    create_parser.add_argument("proposer", help="提案人")
    create_parser.add_argument("decision_type", nargs="?", default=DecisionType.STRATEGIC.value, help="决策类型")

    discuss_parser = subparsers.add_parser("discuss", help="添加讨论意见")
    discuss_parser.add_argument("proposal_id", help="提案ID")
    discuss_parser.add_argument("member_id", help="成员ID")
    discuss_parser.add_argument("opinion", help="意见")

    vote_parser = subparsers.add_parser("vote", help="投票")
    vote_parser.add_argument("proposal_id", help="提案ID")
    vote_parser.add_argument("member_id", help="成员ID")
    vote_parser.add_argument("vote", choices=[item.value for item in VoteResult], help="投票结果")
    vote_parser.add_argument("reason", nargs="?", default="", help="投票理由")

    tally_parser = subparsers.add_parser("tally", help="计票")
    tally_parser.add_argument("proposal_id", help="提案ID")

    order_parser = subparsers.add_parser("order", help="董事长直接下令")
    order_parser.add_argument("proposal_id", help="提案ID")
    order_parser.add_argument("order", help="命令")

    list_parser = subparsers.add_parser("list", help="列出提案")
    list_parser.add_argument("status", nargs="?", default=None, help="状态过滤")

    show_parser = subparsers.add_parser("show", help="查看提案")
    show_parser.add_argument("proposal_id", help="提案ID")

    args = parser.parse_args()
    room = BoardRoom()

    if args.command == "create":
        result = room.create_proposal(args.title, args.content, args.proposer, DecisionType(args.decision_type))
    elif args.command == "discuss":
        result = room.add_discussion(args.proposal_id, args.member_id, args.opinion)
    elif args.command == "vote":
        result = room.cast_vote(args.proposal_id, args.member_id, VoteResult(args.vote), args.reason)
    elif args.command == "tally":
        result = room.tally_votes(args.proposal_id)
    elif args.command == "order":
        result = room.direct_order(args.proposal_id, args.order)
    elif args.command == "list":
        result = room.list_proposals(args.status)
    elif args.command == "show":
        result = room.get_proposal(args.proposal_id)
    else:
        parser.print_help()
        return

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
