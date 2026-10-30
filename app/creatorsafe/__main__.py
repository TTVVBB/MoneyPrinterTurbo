"""Run from the repository root: python -m app.creatorsafe --help."""
import argparse
import sys

from app.creatorsafe.workflow import CHECKS, Workflow


def main():
    parser = argparse.ArgumentParser(description="CreatorSafe Shorts: render, review, export; no uploading")
    parser.add_argument("--root", default="storage/creatorsafe")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("plan")
    for action in ("approve", "reject"):
        review = sub.add_parser(action)
        review.add_argument("job")
        review.add_argument("--reviewer", required=True)
        review.add_argument("--notes", required=True)
    export = sub.add_parser("export")
    export.add_argument("job")
    export.add_argument("destination")
    args = parser.parse_args()
    workflow = Workflow(args.root)
    try:
        if args.command == "prepare":
            from app.creatorsafe.engine import render
            print(workflow.prepare(args.plan, render))
        elif args.command == "approve":
            if not sys.stdin.isatty():
                raise ValueError("Approval requires an interactive human review session")
            print(f"Watch {workflow.job_dir(args.job) / 'final.mp4'} and inspect plan.json, evidence and review.json.")
            checks = [check for check in CHECKS if input(f"Confirm {check} [yes/no]: ").strip().lower() == "yes"]
            if input(f"Type APPROVE {args.job}: ").strip() != f"APPROVE {args.job}":
                raise ValueError("Approval cancelled")
            workflow.approve(args.job, args.reviewer, args.notes, checks)
            print("Approved exact package for manual export. No video uploaded.")
        elif args.command == "reject":
            workflow.reject(args.job, args.reviewer, args.notes)
            print("Rejected. Revise the plan and create a new job.")
        else:
            print(workflow.export(args.job, args.destination))
    except (ValueError, OSError) as error:
        parser.exit(1, f"CreatorSafe: {error}\n")


if __name__ == "__main__":
    main()
