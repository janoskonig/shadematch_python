#!/usr/bin/env python3
"""Freeze the versioned 10-cluster partition used for match drawing.

Protocol requirement: a colour's cluster membership is FIXED for the whole
study period — the assignment is computed once, written to a versioned JSON
artifact (committed to the repo), and the app only ever loads that file
(app/clusters.py match_cluster_*). Re-running with the same catalog and
version reproduces the identical file (seed 42, deterministic ordering).

Scope: the even-coverage background gamut only. Xiao skin-zone targets
(classification 'even_gamut_v2_skin') are EXCLUDED from matches by design —
their densified cluster would distort the cluster-balanced estimand; skin
returns in the spectral-mixing version.

Entries are keyed by catalog_order + RGB (portable across DBs whose ids
differ, e.g. local sqlite vs prod Postgres); the loader verifies the RGB so
silent catalog drift is caught.

Usage:
    PYTHONPATH=. python3 scripts/freeze_match_clusters.py [--version mc-v1] [--commit]

Without --commit it prints the summary and diffs against an existing file.
"""
import argparse
import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--version', default='mc-v1')
    ap.add_argument('--commit', action='store_true', help='write the JSON artifact')
    args = ap.parse_args()

    from app import create_app
    from app.models import TargetColor
    from app.clusters import _kmeans, _region_name
    from app.regions import _srgb_to_lab
    from app.gamification import target_color_sum_drop

    app = create_app()
    with app.app_context():
        rows = [
            tc for tc in TargetColor.query
            .filter_by(color_type='gamut')
            .order_by(TargetColor.catalog_order.asc()).all()
            if tc.classification != 'even_gamut_v2_skin'
            and target_color_sum_drop(tc) is not None
        ]
        if len(rows) < 10:
            raise SystemExit('too few eligible targets (%d)' % len(rows))

        X = np.array([_srgb_to_lab(tc.r, tc.g, tc.b) for tc in rows])
        assign, C = _kmeans(X, 10, seed=42)

        order = sorted(range(10), key=lambda k: (C[k][0], C[k][1], C[k][2]))
        relabel = {old: 'c%d' % new for new, old in enumerate(order)}
        seen = {}
        names = {}
        centroids = {}
        for i in range(10):
            Lc, ac, bc = C[order[i]]
            nm = _region_name(float(Lc), float(ac), float(bc))
            seen[nm] = seen.get(nm, 0) + 1
            if seen[nm] > 1:
                nm = '%s %d.' % (nm, seen[nm])
            names['c%d' % i] = nm
            centroids['c%d' % i] = [round(float(v), 3) for v in C[order[i]]]

        entries = [
            {'catalog_order': tc.catalog_order, 'rgb': [tc.r, tc.g, tc.b],
             'cluster': relabel[int(k)]}
            for tc, k in zip(rows, assign)
        ]

        payload = {
            'version': args.version,
            'algorithm': 'kmeans(CIELAB, k=10, seed=42) over even-coverage gamut '
                         '(classification != even_gamut_v2_skin), recipe-complete, '
                         'ordered by catalog_order; relabelled by centroid (L,a,b)',
            'n_targets': len(entries),
            'names': names,
            'centroids': centroids,
            'entries': entries,
        }

        from collections import Counter
        sizes = Counter(e['cluster'] for e in entries)
        print('version:', args.version, '| targets:', len(entries))
        print('cluster sizes:', dict(sorted(sizes.items())))
        print('names:', names)

        out = REPO / 'data' / ('match_clusters_%s.json' % args.version)
        if out.exists():
            old = json.loads(out.read_text(encoding='utf-8'))
            same = old.get('entries') == entries
            print('existing file:', 'IDENTICAL assignment' if same else 'DIFFERS!')
        if args.commit:
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n',
                           encoding='utf-8')
            print('WROTE', out)
        else:
            print('dry-run (use --commit to write)')


if __name__ == '__main__':
    main()
