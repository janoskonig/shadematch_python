"""Build a per-pigment dataset from the Hyperspectral Pigments speclib (Zenodo
5592485) for gamut analysis and the /spectral palette.

For every unique Kremer pigment (deduped by article number) we take its masstone
(sh-1) reflectance as the mixing primitive — exactly what the app already does for
the original five — resample it to the engine's 38-bin 380-750 nm grid, and tag it
with identity (pnumber, name, group, desc), masstone colour (Lab/sRGB/hue/chroma),
and a relative tinting index from the most-dilute tint (sh-4).

The dataset has NO white pigment, so we inject the shipped Titanium White
(static/pigments/titanium white.txt) as the lightness endmember.

Tinting strengths are placed on the SAME absolute scale the app already ships:
a single divisor maps the library's black/red/yellow/blue onto the shipped
{2.7, 2.1, 1.0, 1.7}; the original five are then pinned to their exact shipped
values so nothing in the current /spectral experience regresses.

Output: data/pigments_library.json  (shared by the optimizer and the app).
Run:    python3 scripts/gamut/build_pigments.py
"""
import json
import os

import numpy as np
import pandas as pd

from app import spectral_km as E
from scripts.gamut.envi_speclib import load_speclib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ZEN = os.path.join(ROOT, 'data', 'zenodo_5592485')
WHITE_TXT = os.path.join(ROOT, 'static', 'pigments', 'titanium white.txt')
OUT = os.path.join(ROOT, 'data', 'pigments_library.json')

VIS = (E.WAVELENGTHS >= 405) & (E.WAVELENGTHS <= 700)

# The five shipped bases, by Kremer article number (white has none — it is not in
# the dataset), with their exact shipped tinting strengths (spectral_mixer.js:43).
SHIPPED = {
    'white':  {'pnumber': None,  'ts': 0.1},
    'black':  {'pnumber': 24100, 'ts': 2.7},
    'red':    {'pnumber': 21120, 'ts': 2.1},
    'yellow': {'pnumber': 21010, 'ts': 1.0},
    'blue':   {'pnumber': 45010, 'ts': 1.7},
}


def ks_mean_vis(R38):
    return float(E.ks(np.clip(R38, 1e-4, 1.0))[VIS].mean())


def load_white():
    wl, refl = [], []
    with open(WHITE_TXT) as f:
        for line in f:
            t = line.split()
            if len(t) == 2:
                try:
                    wl.append(float(t[0])); refl.append(float(t[1]) / 100.0)
                except ValueError:
                    pass
    order = np.argsort(wl)
    R38 = E.resample_to_grid(np.array(wl)[order], np.array(refl)[order])
    return R38


def make_record(prefix, pn, name, group, desc, masstone, ks_dilute):
    col = E.SpectralColor(masstone)
    lab = col.lab
    return {
        'prefix': prefix, 'pnumber': pn, 'name': name, 'group': group, 'desc': desc,
        'R': [round(float(x), 6) for x in masstone],
        'lab': [round(float(x), 3) for x in lab.tolist()],
        'srgb': col.sRGB,
        'hue': round(float(np.degrees(np.arctan2(lab[2], lab[1])) % 360.0), 1),
        'chroma': round(float(np.hypot(lab[1], lab[2])), 2),
        '_ks_dilute': ks_dilute,
    }


def main():
    s = load_speclib(os.path.join(ZEN, '__speclib_averages.hdr'),
                     os.path.join(ZEN, '__speclib_averages.sli'))
    wl, names, R = s['wl'], s['names'], s['R']

    xl = pd.read_excel(os.path.join(ZEN, '__pigmentlistZenodo.xls'))
    xl.columns = ['pnumber', 'pname', 'desc', 'prefix', 'patch', 'group', 'fileprefix']
    meta = {str(r.prefix): r for r in xl.itertuples()}

    by_prefix = {}
    for i, nm in enumerate(names):
        pref, sh = nm.rsplit('_', 1)
        by_prefix.setdefault(pref, {})[sh] = i

    # One record per unique Kremer article number (first patch wins).
    records, seen_pn = [], set()
    for pref, shades in by_prefix.items():
        m = meta.get(pref)
        if m is None or set(shades) != {'sh1', 'sh2', 'sh3', 'sh4'}:
            continue
        pn = int(m.pnumber) if str(m.pnumber).isdigit() else str(m.pnumber)
        if pn in seen_pn:
            continue
        seen_pn.add(pn)
        masstone = E.resample_to_grid(wl, R[shades['sh1']])
        dilute = E.resample_to_grid(wl, R[shades['sh4']])
        grp = '' if (m.group is None or (isinstance(m.group, float) and np.isnan(m.group))) else str(m.group).strip()
        records.append(make_record(pref, pn, str(m.pname).strip(), grp,
                                    str(m.desc).strip(), masstone, ks_mean_vis(dilute)))

    bypn = {r['pnumber']: r for r in records}

    # Single tinting divisor: map library black/red/yellow/blue onto shipped TS.
    # divisor = geomean(raw sh-4 K/S of the 4) / geomean(shipped TS of the 4).
    raw4 = [bypn[SHIPPED[k]['pnumber']]['_ks_dilute'] for k in ('black', 'red', 'yellow', 'blue')]
    tgt4 = [SHIPPED[k]['ts'] for k in ('black', 'red', 'yellow', 'blue')]
    divisor = float(np.exp(np.mean(np.log(raw4))) / np.exp(np.mean(np.log(tgt4))))

    for r in records:
        r['tinting'] = round(r.pop('_ks_dilute') / divisor, 4)

    # Pin the four shipped library bases to their exact shipped TS (zero regression).
    base_keys = {}
    for key in ('black', 'red', 'yellow', 'blue'):
        pn = SHIPPED[key]['pnumber']
        bypn[pn]['tinting'] = SHIPPED[key]['ts']
        base_keys[key] = pn

    # Inject Titanium White (not in the dataset) as the lightness endmember.
    white_R = load_white()
    wcol = E.SpectralColor(white_R)
    wlab = wcol.lab
    white_rec = {
        'prefix': 'TITANIUM_WHITE', 'pnumber': 'white', 'name': 'titanium white',
        'group': 'White', 'desc': 'shipped reference white (not in Zenodo dataset)',
        'R': [round(float(x), 6) for x in white_R],
        'lab': [round(float(x), 3) for x in wlab.tolist()],
        'srgb': wcol.sRGB,
        'hue': round(float(np.degrees(np.arctan2(wlab[2], wlab[1])) % 360.0), 1),
        'chroma': round(float(np.hypot(wlab[1], wlab[2])), 2),
        'tinting': SHIPPED['white']['ts'],
    }
    records.insert(0, white_rec)
    base_keys['white'] = 'white'

    records.sort(key=lambda r: (r['group'], r['name']))
    with open(OUT, 'w') as f:
        json.dump({
            'grid_nm': [int(x) for x in E.WAVELENGTHS],
            'tinting_divisor': divisor,
            'shipped_bases': {k: SHIPPED[k]['pnumber'] if k != 'white' else 'white'
                              for k in SHIPPED},
            'n': len(records),
            'pigments': records,
        }, f)

    print(f'wrote {len(records)} pigments -> {OUT}')
    print(f'tinting divisor = {divisor:.4f}')
    print('\nShipped-base check:')
    for key in ('white', 'black', 'red', 'yellow', 'blue'):
        pn = base_keys[key]
        r = [x for x in records if x['pnumber'] == pn][0]
        print(f"  {key:7s} pn={str(pn):6s} {r['name'][:30]:30s} "
              f"TS={r['tinting']:.3f} lab=({r['lab'][0]:.0f},{r['lab'][1]:.0f},{r['lab'][2]:.0f}) "
              f"hue={r['hue']:5.1f} C={r['chroma']:.1f} srgb={r['srgb']}")


if __name__ == '__main__':
    main()
