import pytz

_TZ_TO_CC = {}
for _cc, _tzs in pytz.country_timezones.items():
    for _tz in _tzs:
        _TZ_TO_CC[_tz] = _cc

COUNTRY_NAMES = {
    'AD': 'Andorra', 'AE': 'UAE', 'AF': 'Afghanistan', 'AG': 'Antigua & Barbuda',
    'AL': 'Albania', 'AM': 'Armenia', 'AO': 'Angola', 'AR': 'Argentina',
    'AT': 'Austria', 'AU': 'Australia', 'AZ': 'Azerbaijan', 'BA': 'Bosnia & Herzegovina',
    'BD': 'Bangladesh', 'BE': 'Belgium', 'BG': 'Bulgaria', 'BH': 'Bahrain',
    'BN': 'Brunei', 'BO': 'Bolivia', 'BR': 'Brazil', 'BY': 'Belarus',
    'CA': 'Canada', 'CD': 'DR Congo', 'CH': 'Switzerland', 'CL': 'Chile',
    'CN': 'China', 'CO': 'Colombia', 'CR': 'Costa Rica', 'CU': 'Cuba',
    'CY': 'Cyprus', 'CZ': 'Czechia', 'DE': 'Germany', 'DK': 'Denmark',
    'DO': 'Dominican Republic', 'DZ': 'Algeria', 'EC': 'Ecuador', 'EE': 'Estonia',
    'EG': 'Egypt', 'ES': 'Spain', 'ET': 'Ethiopia', 'FI': 'Finland',
    'FR': 'France', 'GB': 'United Kingdom', 'GE': 'Georgia', 'GH': 'Ghana',
    'GR': 'Greece', 'GT': 'Guatemala', 'HK': 'Hong Kong', 'HN': 'Honduras',
    'HR': 'Croatia', 'HU': 'Hungary', 'ID': 'Indonesia', 'IE': 'Ireland',
    'IL': 'Israel', 'IN': 'India', 'IQ': 'Iraq', 'IR': 'Iran',
    'IS': 'Iceland', 'IT': 'Italy', 'JM': 'Jamaica', 'JO': 'Jordan',
    'JP': 'Japan', 'KE': 'Kenya', 'KG': 'Kyrgyzstan', 'KH': 'Cambodia',
    'KR': 'South Korea', 'KW': 'Kuwait', 'KZ': 'Kazakhstan', 'LB': 'Lebanon',
    'LK': 'Sri Lanka', 'LT': 'Lithuania', 'LU': 'Luxembourg', 'LV': 'Latvia',
    'LY': 'Libya', 'MA': 'Morocco', 'MD': 'Moldova', 'ME': 'Montenegro',
    'MK': 'North Macedonia', 'MM': 'Myanmar', 'MN': 'Mongolia', 'MO': 'Macau',
    'MT': 'Malta', 'MX': 'Mexico', 'MY': 'Malaysia', 'MZ': 'Mozambique',
    'NG': 'Nigeria', 'NI': 'Nicaragua', 'NL': 'Netherlands', 'NO': 'Norway',
    'NP': 'Nepal', 'NZ': 'New Zealand', 'OM': 'Oman', 'PA': 'Panama',
    'PE': 'Peru', 'PH': 'Philippines', 'PK': 'Pakistan', 'PL': 'Poland',
    'PR': 'Puerto Rico', 'PS': 'Palestine', 'PT': 'Portugal', 'PY': 'Paraguay',
    'QA': 'Qatar', 'RO': 'Romania', 'RS': 'Serbia', 'RU': 'Russia',
    'RW': 'Rwanda', 'SA': 'Saudi Arabia', 'SE': 'Sweden', 'SG': 'Singapore',
    'SI': 'Slovenia', 'SK': 'Slovakia', 'SN': 'Senegal', 'SV': 'El Salvador',
    'SY': 'Syria', 'TH': 'Thailand', 'TN': 'Tunisia', 'TR': 'Turkey',
    'TW': 'Taiwan', 'TZ': 'Tanzania', 'UA': 'Ukraine', 'UG': 'Uganda',
    'US': 'United States', 'UY': 'Uruguay', 'UZ': 'Uzbekistan', 'VE': 'Venezuela',
    'VN': 'Vietnam', 'ZA': 'South Africa', 'ZW': 'Zimbabwe',
}


def tz_to_country(tz_name):
    if not tz_name or not isinstance(tz_name, str):
        return None, None
    cc = _TZ_TO_CC.get(tz_name)
    if not cc:
        return None, None
    return cc, COUNTRY_NAMES.get(cc, cc)
