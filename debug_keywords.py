"""
Debug: Print out what keywords are generated for each loan type
"""

LOAN_KEYWORDS = {
    'personal': {
        'primary': ['personal-loan', 'personal_loan', 'personalloan', 'pl-'],
        'variations': ['unsecured-loan', 'consumer-loan', 'cash-loan'],
        'phrases': ['instant personal', 'pre-approved personal'],
        'codes': ['/pl/', '/personal-loan/', '/consumer-loan/']
    },
    'home': {
        'primary': ['home-loan', 'home_loan', 'homeloan', 'hl-'],
        'variations': ['mortgage', 'property-loan', 'house-loan'],
        'phrases': ['home purchase', 'residential property'],
        'codes': ['/hl/', '/home-loan/', '/mortgage/']
    },
    'auto': {
        'primary': ['car-loan', 'auto-loan', 'vehicle-loan', 'al-'],
        'variations': ['automobile-loan', 'motor-loan'],
        'phrases': ['new car', 'used car'],
        'codes': ['/al/', '/car-loan/', '/vehicle-loan/']
    },
}

def get_loan_keywords(loan_type):
    """Extract and normalize loan keywords"""
    loan_bundle = LOAN_KEYWORDS.get(loan_type.lower(), {})
    all_loan_keywords = []
    for key in ['primary', 'variations', 'phrases', 'codes']:
        all_loan_keywords.extend(loan_bundle.get(key, []))
    
    # Add basic loan type term
    all_loan_keywords.append(loan_type.lower())
    all_loan_keywords.append(f"{loan_type.lower()} loan")
    
    # Normalize loan keywords for matching
    all_loan_keywords = [kw.lower().replace('-', ' ').replace('_', ' ') for kw in all_loan_keywords]
    all_loan_keywords = list(set(all_loan_keywords))  # Remove duplicates
    
    return all_loan_keywords

def filter_generic_keywords(keywords):
    """Remove generic keywords"""
    return [
        kw for kw in keywords 
        if kw not in ['loan', 'loans', 'borrow', 'borrowing']
    ]

print("="*70)
print("PERSONAL LOAN KEYWORDS")
print("="*70)
personal_keywords = get_loan_keywords('personal')
print(f"\nAll keywords ({len(personal_keywords)}):")
for kw in sorted(personal_keywords):
    print(f"  - '{kw}'")

personal_specific = filter_generic_keywords(personal_keywords)
print(f"\nSpecific keywords ({len(personal_specific)}):")
for kw in sorted(personal_specific):
    print(f"  - '{kw}'")

print("\n" + "="*70)
print("HOME LOAN KEYWORDS")
print("="*70)
home_keywords = get_loan_keywords('home')
print(f"\nAll keywords ({len(home_keywords)}):")
for kw in sorted(home_keywords):
    print(f"  - '{kw}'")

home_specific = filter_generic_keywords(home_keywords)
print(f"\nSpecific keywords ({len(home_specific)}):")
for kw in sorted(home_specific):
    print(f"  - '{kw}'")

print("\n" + "="*70)
print("TESTING URL MATCHING")
print("="*70)

test_urls = [
    ('https://www.hdfcbank.com/personal/borrow/popular-loans/personal-loan/terms', 'Personal Loan Terms', 'personal'),
    ('https://www.hdfcbank.com/personal/borrow/popular-loans/home-loan/terms', 'Home Loan Terms', 'home'),
]

for url, title, expected_type in test_urls:
    combined = f"{url.lower()} {title.lower()}".replace('-', ' ').replace('_', ' ')
    print(f"\nURL: {url[:60]}")
    print(f"Expected type: {expected_type}")
    print(f"Combined (normalized): {combined[:80]}")
    
    # Test personal keywords
    matches_personal = []
    for kw in personal_specific:
        if kw in combined:
            matches_personal.append(kw)
    
    # Test home keywords
    matches_home = []
    for kw in home_specific:
        if kw in combined:
            matches_home.append(kw)
    
    print(f"  Personal keywords matched: {matches_personal if matches_personal else 'NONE'}")
    print(f"  Home keywords matched: {matches_home if matches_home else 'NONE'}")
    
    if expected_type == 'personal' and matches_personal and not matches_home:
        print("  ✅ CORRECT: Matches personal only")
    elif expected_type == 'home' and matches_home and not matches_personal:
        print("  ✅ CORRECT: Matches home only")
    else:
        print("  ❌ ERROR: Incorrect matching!")
