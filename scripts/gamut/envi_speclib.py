"""Parse the ENVI spectral library (.hdr + .sli) from the Hyperspectral Pigments
dataset (Zenodo 5592485, H. Deborah 2022). Returns per-spectrum reflectance.

.hdr is an ENVI ASCII header; .sli is raw float32, shape (lines, samples) =
(n_spectra, n_bands), interleave bsq, little-endian (byte order 0)."""
import re
import numpy as np


def _parse_brace_list(text, key):
    m = re.search(key + r'\s*=\s*\{(.*?)\}', text, re.S)
    if not m:
        return None
    body = m.group(1)
    return [tok.strip() for tok in body.split(',') if tok.strip() != '']


def load_speclib(hdr_path, sli_path):
    with open(hdr_path, 'r') as f:
        hdr = f.read()
    samples = int(re.search(r'samples\s*=\s*(\d+)', hdr).group(1))
    lines = int(re.search(r'lines\s*=\s*(\d+)', hdr).group(1))
    wl = np.array([float(x) for x in _parse_brace_list(hdr, 'wavelength')], dtype=float)
    names = _parse_brace_list(hdr, 'spectra names')
    pnum = _parse_brace_list(hdr, 'pnumber')
    data = np.fromfile(sli_path, dtype='<f4').astype(float).reshape(lines, samples)
    return {'wl': wl, 'names': names, 'pnumber': pnum, 'R': data,
            'samples': samples, 'lines': lines}


if __name__ == '__main__':
    import os
    d = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'zenodo_5592485')
    s = load_speclib(os.path.join(d, '__speclib_averages.hdr'),
                     os.path.join(d, '__speclib_averages.sli'))
    print('lines(spectra)=%d  samples(bands)=%d' % (s['lines'], s['samples']))
    print('wl: %.2f .. %.2f nm  (n=%d)' % (s['wl'][0], s['wl'][-1], len(s['wl'])))
    print('names: %d  pnumber: %d' % (len(s['names'] or []), len(s['pnumber'] or [])))
    print('first 8 names:', s['names'][:8] if s['names'] else None)
    print('R range: %.4f .. %.4f' % (np.nanmin(s['R']), np.nanmax(s['R'])))
    print('unique pnumbers:', len(set(s['pnumber'])) if s['pnumber'] else None)
