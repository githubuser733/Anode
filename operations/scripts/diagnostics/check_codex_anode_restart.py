"""Read a captured Windows event timeline; can fail on the reported forced repair."""
import argparse
import json
from pathlib import Path

def forced_repairs(rows):
    return [r for r in rows if r['id']==603 and 'OpenAI.Codex_' in r['message']
            and 'ForceTargetApplicationShutdownOption' in r['message'] and 'RepairAppRegistrationOption' in r['message']]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path)
    parser.add_argument('--assert-no-repairs',action='store_true')
    args=parser.parse_args()
    rows=json.loads(args.events.read_text('utf-8-sig'))
    found=forced_repairs(rows)
    print(json.dumps({'result':'FORCED_CODEX_REPAIR_FOUND' if found else 'NO_FORCED_CODEX_REPAIR',
        'count':len(found),'records':[r['record'] for r in found]},ensure_ascii=True))
    return 1 if args.assert_no_repairs and found else 0

if __name__=='__main__':raise SystemExit(main())
