"""
FIXED Easy-to-Use Bank Scraper Interface
=========================================
This version includes all the fixes for irrelevant results
"""

import asyncio
import argparse
import json
import logging
import time
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse, unquote

import aiohttp
from bs4 import BeautifulSoup
import PyPDF2
import pickle  # STEP 29: For caching
from datetime import datetime  # STEP 29: For cache expiry

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ==============================================================================
# SEARCH & FILTER CONSTANTS
# ==============================================================================

# URLs containing any of these substrings are rejected immediately
BLACKLIST = {
    'decease', 'death', 'deceased', 'demise', 'obituary',
    'insurance-claim', 'health-insurance', 'life-insurance', 'term-insurance',
    'credit-card', 'debit-card', 'atm', 'netbanking', 'mobile-banking',
    'savings-account', 'current-account', 'salary-account', 'zero-balance',
    'fixed-deposit', 'fd', 'recurring-deposit', 'rd', 'time-deposit',
    'mutual-fund', 'mf', 'trading', 'demat', 'stock', 'equity', 'shares',
    'forex', 'remittance', 'foreign-exchange', 'currency',
    'grievance', 'complaint', 'feedback', 'contact-us', 'customer-care',
    'careers', 'jobs', 'recruitment', 'vacancy', 'internship',
    'branch-locator', 'atm-locator', 'ifsc-code', 'micr-code',
    'locker', 'safe-deposit-box', 'vault',
    'nri-services', 'nre-account', 'nro-account', 'fcnr',
    'cheque-book', 'passbook', 'statement',
    'kyc', 'pan-card', 'aadhar', 'know-your-customer'
}

# Loan-type specific keyword bundles for URL/token matching
LOAN_KEYWORDS = {
    'personal': {
        'primary': ['personal-loan', 'personal_loan', 'personalloan', 'pl-'],
        'variations': ['unsecured-loan', 'consumer-loan', 'cash-loan'],
        'phrases': ['instant personal', 'pre-approved personal', 'quick personal'],
        'codes': ['/pl/', '/personal-loan/', '/consumer-loan/']
    },
    'home': {
        'primary': ['home-loan', 'home_loan', 'homeloan', 'hl-', 'housing-loan'],
        'variations': ['mortgage', 'property-loan', 'house-loan'],
        'phrases': ['home purchase', 'residential property', 'house finance'],
        'codes': ['/hl/', '/home-loan/', '/housing-loan/', '/mortgage/']
    },
    'auto': {
        'primary': ['car-loan', 'auto-loan', 'vehicle-loan', 'al-', 'cl-'],
        'variations': ['automobile-loan', 'motor-loan', 'car-finance'],
        'phrases': ['new car', 'used car', 'car purchase', 'vehicle finance'],
        'codes': ['/al/', '/cl/', '/car-loan/', '/auto-loan/', '/vehicle-loan/']
    },
    'education': {
        'primary': ['education-loan', 'student-loan', 'study-loan', 'el-'],
        'variations': ['academic-loan', 'higher-education', 'overseas-education'],
        'phrases': ['education finance', 'student finance', 'study abroad'],
        'codes': ['/el/', '/education-loan/', '/student-loan/']
    },
    'business': {
        'primary': ['business-loan', 'sme-loan', 'msme-loan', 'bl-'],
        'variations': ['enterprise-loan', 'working-capital', 'business-finance'],
        'phrases': ['small business', 'business expansion', 'sme finance'],
        'codes': ['/bl/', '/sme/', '/msme/', '/business-loan/']
    }
}

# Enhanced T&C related keyword buckets
TNC_KEYWORDS = {
    'primary': [
        'terms-and-conditions', 'terms-conditions', 'tnc', 't&c', 't-c',
        'mitc', 'most-important-terms', 'key-terms', 'important-terms'
    ],
    'charges': [
        'schedule-of-charges', 'schedule-charges', 'soc', 'tariff',
        'fees-and-charges', 'fees-charges', 'charges-tariff',
        'pricing', 'charges-fees', 'fee-structure'
    ],
    'rates': [
        'interest-rate', 'interest-rates', 'rate-of-interest', 'roi',
        'apr', 'annual-percentage-rate', 'effective-interest',
        'processing-fee', 'prepayment-charges'
    ],
    'documents': [
        'loan-agreement', 'sanction-letter', 'offer-letter',
        'disclosure', 'fact-sheet', 'key-facts', 'product-disclosure',
        'eligibility', 'documentation', 'documents-required'
    ]
}

# Variations used for text and URL analysis per loan type
LOAN_VARIATIONS = {
    'personal': {
        'names': ['personal loan', 'unsecured personal loan', 'consumer loan'],
        'keywords': ['salary-based loan', 'pre-approved personal loan'],
        'codes': ['PL', 'personal-loan', 'personalloan'],
        'related': ['cash loan', 'instant personal loan']
    },
    'home': {
        'names': ['home loan', 'housing loan', 'mortgage loan', 'property loan'],
        'keywords': ['house purchase loan', 'residential property loan'],
        'codes': ['HL', 'home-loan', 'homeloan'],
        'related': ['loan against property', 'construction loan', 'plot loan']
    },
    'auto': {
        'names': ['car loan', 'auto loan', 'vehicle loan', 'automobile loan'],
        'keywords': ['new car loan', 'used car loan', 'car finance'],
        'codes': ['AL', 'CL', 'car-loan', 'autoloan'],
        'related': ['two-wheeler loan', 'commercial vehicle loan']
    },
    'education': {
        'names': ['education loan', 'student loan', 'study loan', 'academic loan'],
        'keywords': ['higher education loan', 'overseas education loan'],
        'codes': ['EL', 'education-loan', 'studentloan'],
        'related': ['skill development loan', 'career loan']
    },
    'business': {
        'names': ['business loan', 'SME loan', 'MSME loan', 'enterprise loan'],
        'keywords': ['small business loan', 'working capital loan'],
        'codes': ['BL', 'business-loan', 'sme-loan'],
        'related': ['business expansion loan', 'machinery loan', 'overdraft facility']
    }
}

# Pre-compute flattened TNC term list for reuse
def _flatten_tnc_terms() -> List[str]:
    terms: List[str] = []
    for values in TNC_KEYWORDS.values():
        terms.extend(values)
    return terms


TNC_TERMS = [term.lower() for term in _flatten_tnc_terms()]
TNC_TERMS_TEXT = [term.replace('-', ' ').replace('_', ' ') for term in TNC_TERMS]


def _match_blacklist(text: str) -> Optional[str]:
    """Return the first blacklist pattern found in text or None."""
    for pattern in BLACKLIST:
        if pattern and pattern in text:
            return pattern
    return None


def _loan_bundle(loan_type: str) -> Dict[str, List[str]]:
    loan_key = loan_type.lower()
    return LOAN_KEYWORDS.get(loan_key, LOAN_KEYWORDS.get('personal')).copy()


def _loan_variation_bundle(loan_type: str) -> Dict[str, List[str]]:
    loan_key = loan_type.lower()
    default = {
        'names': [f'{loan_key} loan'],
        'keywords': [f'{loan_key} financing'],
        'codes': [f'{loan_key}-loan'],
        'related': []
    }
    return LOAN_VARIATIONS.get(loan_key, default).copy()


def _collect_text_terms(loan_type: str) -> List[str]:
    """Gather terms used to validate textual relevance for a given loan type."""
    bundle = _loan_bundle(loan_type)
    variations = _loan_variation_bundle(loan_type)
    terms: List[str] = []
    terms.extend(bundle.get('primary', []))
    terms.extend(bundle.get('variations', []))
    terms.extend(bundle.get('phrases', []))
    terms.extend(variations.get('names', []))
    terms.extend(variations.get('keywords', []))
    terms.extend(variations.get('related', []))
    terms.append(f"{loan_type.lower()} loan")
    terms.append(loan_type.lower())
    terms.append('loan')
    normalized: List[str] = []
    for term in terms:
        cleaned = term.lower().strip()
        if not cleaned:
            continue
        normalized.append(cleaned.replace('-', ' '))
        normalized.append(cleaned.replace('_', ' '))
    return list(dict.fromkeys([t.strip() for t in normalized if t.strip()]))


def _collect_url_terms(loan_type: str) -> List[str]:
    """Gather URL-oriented tokens to match loan type hints."""
    bundle = _loan_bundle(loan_type)
    variations = _loan_variation_bundle(loan_type)
    terms: List[str] = []
    for key in ('primary', 'variations', 'phrases', 'codes'):
        terms.extend(bundle.get(key, []))
    terms.extend(variations.get('codes', []))
    terms.extend(variations.get('names', []))
    terms.extend(variations.get('keywords', []))
    terms.extend(variations.get('related', []))
    terms.append(loan_type.lower())
    terms.append(f"{loan_type.lower()}-loan")
    return list(dict.fromkeys([term.lower() for term in terms if term]))

# ==============================================================================
# IMPROVED SEMANTIC FILTER
# ==============================================================================

class ImprovedSemanticFilter:
    """Enhanced filter that prevents irrelevant results"""
    
    def __init__(self, loan_type: str = "personal"):
        self.loan_type = loan_type.lower()
        logger.info(f"🎯 Initialized filter for {loan_type} loans")
    
    def is_relevant(self, text: str, url: str = "", threshold: float = 0.25) -> Tuple[bool, float]:
        """Check relevance with extended keyword intelligence and blacklist enforcement"""
        if not text or len(text) < 100:
            return False, 0.0

        text_lower = text.lower()
        url_lower = url.lower()
        loan_terms = _collect_text_terms(self.loan_type)

        blacklist_hit = _match_blacklist(url_lower)
        if blacklist_hit:
            # STEP 20: Enhanced rejection logging
            logger.info(f"🚫 REJECTED: {url[:80]}")
            logger.info(f"   Reason: Blacklisted keyword")
            logger.info(f"   Details: '{blacklist_hit}' found in URL")
            return False, 0.0

        wrong_product_keywords = [
            'credit card', 'creditcard', 'debit card',
            'savings account', 'current account',
            'fixed deposit', 'recurring deposit',
            'insurance policy', 'life insurance'
        ]

        for wrong_prod in wrong_product_keywords:
            if wrong_prod in url_lower or wrong_prod in text_lower[:600]:
                loan_mentions = sum(1 for term in loan_terms[:6] if term in text_lower[:1500])
                if loan_mentions < 2:
                    # STEP 20: Enhanced rejection logging
                    logger.info(f"🚫 REJECTED: {url[:80]}")
                    logger.info(f"   Reason: Wrong product type")
                    logger.info(f"   Details: '{wrong_prod}' detected, only {loan_mentions} loan mentions")
                    return False, 0.0
        
        # STEP 10: Loan type variation check - detect if document is about different loan type
        # Get variations for target loan type
        target_variations = LOAN_VARIATIONS.get(self.loan_type, {})
        target_names = target_variations.get('names', [])
        target_keywords = target_variations.get('keywords', [])
        all_target_terms = target_names + target_keywords
        
        # Check against other loan types
        other_loan_types = [lt for lt in LOAN_VARIATIONS.keys() if lt != self.loan_type]
        
        # Count target loan mentions in first 2000 chars
        target_count = sum(1 for term in all_target_terms if term in text_lower[:2000])
        
        # Check if document is dominated by another loan type
        for other_type in other_loan_types:
            other_variations = LOAN_VARIATIONS.get(other_type, {})
            other_names = other_variations.get('names', [])
            other_keywords = other_variations.get('keywords', [])
            all_other_terms = other_names + other_keywords
            
            # Count other loan type mentions
            other_count = sum(1 for term in all_other_terms if term in text_lower[:2000])
            
            # If other loan type is mentioned 2x more than target, reject
            if other_count > target_count * 2 and other_count >= 3:
                # STEP 20: Enhanced rejection logging
                logger.info(f"🚫 REJECTED: {url[:80]}")
                logger.info(f"   Reason: Wrong loan type dominates")
                logger.info(f"   Details: '{other_type}' ({other_count} mentions) vs target ({target_count} mentions)")
                return False, 0.0

        negative_keywords = [
            'deceased', 'death', 'demise', 'claim', 'settlement',
            'insurance claim', 'nominee', 'legal heir',
            'complaint', 'grievance', 'customer care'
        ]

        for neg_kw in negative_keywords:
            if neg_kw in text_lower[:800] or neg_kw in url_lower:
                logger.info(f"❌ Filtered: '{neg_kw}' found in {url[:60]}")
                return False, 0.0

        loan_hits = sum(1 for term in loan_terms[:12] if term in text_lower[:4000])
        loan_score = min(loan_hits / 4.0, 1.0)

        tnc_hits = sum(1 for term in TNC_TERMS_TEXT[:20] if term in text_lower[:4000])
        terms_score = min(tnc_hits / 4.0, 1.0)

        final_score = (loan_score * 0.45 + terms_score * 0.55)
        is_relevant = final_score >= threshold

        if is_relevant:
            logger.info(f"✅ Relevant (score: {final_score:.2f}): {url[:70]}")

        return is_relevant, final_score

# ==============================================================================
# IMPROVED VALIDATOR
# ==============================================================================

class ImprovedValidator:
    """Stricter document validation"""
    
    @staticmethod
    def validate(text: str, bank_name: str, loan_type: str) -> Tuple[bool, Dict]:
        """Validate with strict checks including product type verification"""
        result = {
            'valid': False,
            'reasons': [],
            'scores': {},
            'overall_score': 0.0
        }
        
        if not text or len(text) < 500:
            result['reasons'].append("Content too short")
            return False, result
        
        text_lower = text.lower()
        
        # 1. Negative check - MUST NOT contain these
        negative_terms = ['deceased', 'death claim', 'settlement claim', 'demise']
        for term in negative_terms:
            if term in text_lower[:1000]:
                result['reasons'].append(f"Contains irrelevant term: {term}")
                return False, result
        result['scores']['negative_check'] = 1.0
        
        # STEP 11: Bank competition check - reject if competing banks mentioned more than target
        all_banks = ['HDFC', 'SBI', 'ICICI', 'Axis', 'Kotak', 'Yes Bank', 'IndusInd', 'IDFC First', 'PNB', 'Bank of Baroda']
        competing_banks = [b for b in all_banks if b.lower() != bank_name.lower()]
        
        # Count target bank mentions
        bank_clean = bank_name.lower().replace(' ', '')
        text_clean = text_lower.replace(' ', '')
        target_count = text_clean[:2000].count(bank_clean)
        
        # Count competing bank mentions
        for competitor in competing_banks:
            competitor_clean = competitor.lower().replace(' ', '')
            competitor_count = text_clean[:2000].count(competitor_clean)
            
            if competitor_count > target_count:
                result['reasons'].append(f"Wrong bank: {competitor} mentioned {competitor_count} times vs {bank_name} {target_count} times")
                # STEP 20: Enhanced bank competition logging
                logger.info(f"❌ BANK COMPETITION: Document rejected")
                logger.info(f"   Target: {bank_name} ({target_count} mentions)")
                logger.info(f"   Competitor: {competitor} ({competitor_count} mentions)")
                logger.info(f"   Location: First 2000 chars")
                return False, result
        
        # STEP 12: Strict bank name check - require minimum 3 mentions
        if target_count < 3:
            result['reasons'].append(f"Insufficient bank mentions: {target_count} (need 3+)")
            logger.info(f"❌ Insufficient bank mentions: {bank_name} only {target_count} times (need 3+)")
            return False, result
        
        # 2. CRITICAL: Check for wrong product types in content
        # If document is about credit cards, savings accounts, etc., reject it
        wrong_product_indicators = [
            ('credit card', ['key fact', 'statement', 'annual fee', 'cashback', 'reward point']),
            ('debit card', ['atm', 'pin', 'card number']),
            ('savings account', ['minimum balance', 'passbook', 'chequebook']),
            ('fixed deposit', ['maturity', 'premature withdrawal penalty']),
            ('insurance', ['premium', 'policy', 'claim', 'beneficiary'])
        ]
        
        for product, indicators in wrong_product_indicators:
            # Count how many indicators of this wrong product appear
            indicator_count = sum(1 for ind in indicators if ind in text_lower[:2000])
            # If product name + 2 or more indicators, likely wrong product
            if product in text_lower[:1000] and indicator_count >= 2:
                # Double-check: is the target loan type prominently mentioned?
                loan_mentions = text_lower[:2000].count(loan_type.lower())
                if loan_mentions < 2:  # Loan type not prominent
                    result['reasons'].append(f"Document appears to be about {product}, not {loan_type} loan")
                    return False, result
        
        # 3. Bank name score (target_count already calculated in STEP 12)
        # STEP 12: Scale score with mention count (max score at 5 mentions)
        result['scores']['bank_name'] = min(target_count / 5.0, 1.0)
        
        # 4. Loan type - must be mentioned multiple times in first 2000 chars
        loan_mentions = text_lower[:2000].count(loan_type.lower())
        if loan_mentions == 0:
            result['reasons'].append(f"Loan type '{loan_type}' not found in document")
            return False, result
        result['scores']['loan_type'] = min(loan_mentions / 3.0, 1.0)
        
        # STEP 13: Loan Type Competition Check
        # Check if document is primarily about a different loan type
        other_loan_types = list(LOAN_VARIATIONS.keys())
        if loan_type.lower() in other_loan_types:
            other_loan_types.remove(loan_type.lower())
        
        # Get target loan variations
        target_variations = LOAN_VARIATIONS.get(loan_type.lower(), {}).get('names', [])
        target_count = sum(text_lower[:1500].count(name.lower()) for name in target_variations)
        
        # Check each other loan type
        for other_type in other_loan_types:
            other_variations = LOAN_VARIATIONS.get(other_type, {}).get('names', [])
            other_count = sum(text_lower[:1500].count(name.lower()) for name in other_variations)
            
            # If other loan type mentioned 2x more than target, reject
            if other_count > target_count * 2 and other_count >= 2:
                result['reasons'].append(f"Document primarily about {other_type} loan (mentioned {other_count} times vs {loan_type} {target_count} times)")
                logger.info(f"❌ Loan type competition: {other_type} ({other_count}) > {loan_type} ({target_count})")
                return False, result
        
        # 5. Essential keywords
        # STEP 15: Loan-specific essential keywords
        # Base keywords
        essential_keywords = ['loan', 'interest', 'rate', 'charges', 'fees', 'emi']
        
        # Add loan-specific keywords
        loan_specific = {
            'personal': ['unsecured', 'salary', 'income', 'cibil'],
            'home': ['property', 'mortgage', 'collateral', 'stamp duty'],
            'auto': ['vehicle', 'car', 'rto', 'hypothecation'],
            'education': ['student', 'course', 'university', 'tuition'],
            'business': ['sme', 'msme', 'turnover', 'gst']
        }
        
        if loan_type.lower() in loan_specific:
            essential_keywords.extend(loan_specific[loan_type.lower()])
        
        essential_score = sum(1 for kw in essential_keywords if kw in text_lower) / len(essential_keywords)
        result['scores']['essential'] = essential_score
        
        # 6. Terms keywords
        terms = ['terms', 'conditions', 'agreement', 'tenure']
        terms_score = sum(1 for kw in terms if kw in text_lower) / len(terms)
        result['scores']['terms'] = terms_score
        
        # Overall score
        # STEP 11: Increased bank_name weight from 0.20 to 0.30
        overall = (
            result['scores']['negative_check'] * 0.25 +
            result['scores']['bank_name'] * 0.30 +
            result['scores']['loan_type'] * 0.20 +
            result['scores']['essential'] * 0.15 +
            result['scores']['terms'] * 0.10
        )
        
        result['overall_score'] = overall
        # STEP 14: Increased validation threshold from 0.60 to 0.75 for stricter quality
        result['valid'] = overall >= 0.75
        
        if not result['valid']:
            result['reasons'].append(f"Low validation score: {overall:.2f} (need 0.75+)")
        
        return result['valid'], result

# ==============================================================================
# IMPROVED SEARCH
# ==============================================================================

class ImprovedSearch:
    """Better search query generation"""
    
    DOMAIN_MAP = {
        'HDFC': 'hdfcbank.com',
        'SBI': 'sbi.co.in',
        'ICICI': 'icicibank.com',
        'Axis': 'axisbank.com',
        'Kotak': 'kotak.com',
        'Yes Bank': 'yesbank.in',
        'IndusInd': 'indusind.com',
        'IDFC First': 'idfcfirstbank.com'
    }
    
    @staticmethod
    def generate_queries(bank_name: str, loan_type: str) -> List[str]:
        """Generate focused queries"""
        domain = ImprovedSearch.DOMAIN_MAP.get(bank_name, '')
        
        return [
            f'site:{domain} {loan_type} loan terms conditions PDF',
            f'site:{domain} {loan_type} loan MITC filetype:pdf',
            f'{bank_name} {loan_type} loan schedule charges PDF',
            f'"{bank_name}" "{loan_type} loan" "terms and conditions" filetype:pdf',
            f'{bank_name} {loan_type} loan interest rate fees PDF'
        ]
    
    @staticmethod
    def filter_urls(links: List[Dict], bank_name: str, loan_type: str) -> List[str]:
        """Filter URLs aggressively with strict product type checking"""
        exclude = [
            'deceased', 'claim', 'settlement', 'death',
            'credit-card', 'debit-card', 'savings',
            'complaints', 'customer-care', 'careers',
            'about-us', 'news', 'branch', 'atm',
            'creditcard', 'credit card'  # Handle both formats
        ]

        # Known EMI / calculator patterns (handle specially)
        calculator_patterns = ['emi', 'calculator', 'emi-calculator', 'emi-cal', 'loan-calculator', 'calculate-emi']

        # Wrong product types - MUST reject these
        wrong_products = [
            'credit', 'creditcard', 'debit', 'card', 
            'savings', 'current', 'account', 'deposit',
            'insurance', 'investment', 'mutual', 'fund'
        ]

        include = [loan_type.lower(), 'loan', 'terms', 'conditions', 'charges', 'pdf']
        
        filtered = []
        for link in links:
            url_raw = link.get('url', '')
            url = url_raw.lower()
            title = link.get('title', '').lower()
            combined = f"{url} {title}"
            
            # Decode URL entities for better matching
            from urllib.parse import unquote
            combined_decoded = unquote(combined)
            
            # FIRST: Reject wrong product types (credit cards, savings accounts, etc.)
            # Check if URL/title mentions these products WITHOUT also mentioning the target loan type
            has_wrong_product = False
            for wrong_prod in wrong_products:
                if wrong_prod in combined_decoded:
                    # Only reject if it's clearly about that product (not just mentioning it)
                    # e.g., "credit card terms" should be rejected
                    # but "personal loan credit score" should not
                    if wrong_prod in url or wrong_prod in title:
                        # Make sure it's not a loan document that just mentions cards
                        if loan_type.lower() not in combined_decoded[:100]:  # loan type not prominent
                            has_wrong_product = True
                            logger.info(f"🚫 Excluding wrong product ({wrong_prod}): {url_raw[:80]}")
                            break
            
            if has_wrong_product:
                continue
            
            # Skip if has negative patterns
            if any(pattern in combined_decoded for pattern in exclude):
                logger.info(f"🚫 Excluding negative pattern: {url_raw[:80]}")
                continue

            # Be aggressive about calculator links
            has_calc = any(pattern in combined for pattern in calculator_patterns)
            if has_calc:
                if not any(k in combined for k in ['terms', 'tnc', '.pdf', 'mitc', 'terms-and-conditions']):
                    logger.info(f"🚫 Excluding calculator/tool link: {url_raw[:80]}")
                    continue
            
            # Include if has enough positive patterns
            positive_count = sum(1 for pattern in include if pattern in combined)
            if positive_count >= 2:
                filtered.append(url_raw)
        
        return filtered

# ==============================================================================
# MAIN SCRAPER CLASS
# ==============================================================================

class FixedBankScraper:
    """Fixed scraper with all improvements"""
    
    def __init__(self, google_api_key: str, google_cse_id: str, test_mode: bool = False):
        self.api_key = google_api_key
        self.cse_id = google_cse_id
        self.test_mode = test_mode  # STEP 27
        self.search = ImprovedSearch()
        
        # STEP 29: Initialize cache
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
        
        logger.info("✅ Fixed scraper initialized")
        if test_mode:
            logger.info("🧪 Test mode enabled in FixedBankScraper")
    
    def _get_cache_key(self, bank_name: str, loan_type: str) -> Path:
        """Generate cache filename"""
        key = f"{bank_name.lower().replace(' ', '_')}_{loan_type.lower()}.pkl"
        return self.cache_dir / key
    
    def _get_from_cache(self, bank_name: str, loan_type: str, max_age_hours: int = 24) -> Optional[Dict]:
        """Get cached result if valid"""
        cache_file = self._get_cache_key(bank_name, loan_type)
        
        if not cache_file.exists():
            return None
        
        try:
            # Check age
            file_age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
            if file_age.total_seconds() > (max_age_hours * 3600):
                logger.info(f"💾 Cache expired for {bank_name} {loan_type}")
                return None
            
            # Load cache
            with open(cache_file, 'rb') as f:
                result = pickle.load(f)
            
            logger.info(f"💾 Cache hit for {bank_name} {loan_type}")
            return result
        
        except Exception as e:
            logger.warning(f"💾 Cache read error: {e}")
            return None
    
    def _save_to_cache(self, bank_name: str, loan_type: str, result: Dict):
        """Save result to cache"""
        cache_file = self._get_cache_key(bank_name, loan_type)
        
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(result, f)
            logger.info(f"💾 Cached result for {bank_name} {loan_type}")
        except Exception as e:
            logger.warning(f"💾 Cache write error: {e}")
    
    def clear_cache(self) -> int:
        """Clear all cached results"""
        count = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            cache_file.unlink()
            count += 1
        logger.info(f"💾 Cleared {count} cached results")
        return count
    
    def get_cache_info(self) -> Dict:
        """Get cache statistics"""
        files = []
        for cache_file in self.cache_dir.glob("*.pkl"):
            age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
            files.append({
                'name': cache_file.name,
                'age_hours': round(age.total_seconds() / 3600, 1),
                'size_kb': round(cache_file.stat().st_size / 1024, 1)
            })
        
        return {
            'total_cached': len(files),
            'cache_dir': str(self.cache_dir),
            'files': files
        }
    
    async def scrape_bank(self, bank_name: str, loan_type: str, use_cache: bool = True) -> Dict:
        """Scrape with all fixes applied"""
        # STEP 29: Check cache first
        if use_cache:
            cached = self._get_from_cache(bank_name, loan_type)
            if cached:
                cached['metadata']['from_cache'] = True
                return cached
        
        start_time = time.time()
        
        logger.info(f"\n{'='*70}")
        logger.info(f"🎯 Scraping: {bank_name} {loan_type} loan")
        logger.info(f"{'='*70}\n")
        
        result = {
            'bank': bank_name,
            'loan_type': loan_type,
            'success': False,
            'documents': [],
            'metadata': {
                'pages_crawled': 0,
                'pdfs_found': 0,
                'execution_time_seconds': 0,
                'errors': []
            }
        }
        
        try:
            # Initialize filter
            semantic_filter = ImprovedSemanticFilter(loan_type)
            validator = ImprovedValidator()
            
            # Generate queries
            queries = self.search.generate_queries(bank_name, loan_type)
            logger.info(f"📝 Generated {len(queries)} queries")
            
            # Search and collect URLs
            all_urls = []
            for query in queries[:3]:  # Top 3 queries
                try:
                    urls = await self._search_google(query, bank_name, loan_type)
                    all_urls.extend(urls)
                    await asyncio.sleep(1)  # Rate limit
                except Exception as e:
                    logger.error(f"Search error: {e}")
                    result['metadata']['errors'].append(str(e))
            
            # Deduplicate
            unique_urls = list(set(all_urls))[:8]
            logger.info(f"📊 Processing {len(unique_urls)} unique URLs\n")
            
            result['metadata']['pages_crawled'] = len(unique_urls)
            result['metadata']['pdfs_found'] = sum(1 for u in unique_urls if u.endswith('.pdf'))
            
            # Process each URL
            for url in unique_urls:
                doc = await self._process_url(url, bank_name, loan_type, semantic_filter, validator)
                if doc:
                    result['documents'].append(doc)
            
            # Sort by confidence
            result['documents'].sort(key=lambda x: x['confidence'], reverse=True)
            
            # Mark success
            if result['documents'] and result['documents'][0]['confidence'] > 0.60:
                result['success'] = True
            
            result['metadata']['execution_time_seconds'] = round(time.time() - start_time, 2)
            
            # STEP 29: Cache successful results
            if result['success'] and use_cache and len(result['documents']) > 0:
                self._save_to_cache(bank_name, loan_type, result)
            
            logger.info(f"\n{'='*70}")
            logger.info(f"✅ Complete: {len(result['documents'])} documents")
            if result['documents']:
                logger.info(f"💪 Best confidence: {result['documents'][0]['confidence']:.2%}")
            logger.info(f"⏱️ Time: {result['metadata']['execution_time_seconds']}s")
            logger.info(f"{'='*70}\n")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Fatal error: {e}", exc_info=True)
            result['metadata']['errors'].append(f"Fatal: {str(e)}")
            return result
    
    async def _search_google(self, query: str, bank_name: str, loan_type: str) -> List[str]:
        """Search Google and filter results"""
        if not self.api_key or self.api_key == "YOUR_API_KEY_HERE":
            logger.warning("⚠️ No API key configured")
            return []
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            'key': self.api_key,
            'cx': self.cse_id,
            'q': query,
            'num': 10
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        links = [
                            {'url': item['link'], 'title': item.get('title', ''), 'snippet': item.get('snippet', '')}
                            for item in data.get('items', [])
                        ]
                        
                        filtered_urls = self.search.filter_urls(links, bank_name, loan_type)
                        logger.info(f"✅ Query: {query[:50]}")
                        logger.info(f"   Results: {len(links)} → Filtered: {len(filtered_urls)}")
                        
                        return filtered_urls
                    else:
                        logger.error(f"⚠️ Search API error: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    async def _process_url(
        self,
        url: str,
        bank_name: str,
        loan_type: str,
        semantic_filter,
        validator
    ) -> Optional[Dict]:
        """Process a single URL with strict product type checking"""
        try:
            logger.info(f"🔄 Processing: {url[:70]}")
            
            # Early reject: Check URL for wrong product types
            from urllib.parse import unquote
            url_decoded = unquote(url.lower())
            
            wrong_products = ['creditcard', 'credit card', 'debit card', 
                            'savings account', 'fixed deposit', 'insurance']
            for wrong_prod in wrong_products:
                if wrong_prod in url_decoded:
                    # Only allow if loan type is clearly in URL
                    if loan_type.lower() not in url_decoded:
                        logger.info(f"🚫 Rejecting wrong product URL: {url[:80]}")
                        return None
            
            text = None
            is_pdf = url.lower().endswith('.pdf')

            # Early-exclude obvious calculator/tool pages unless they include terms or pdf
            if any(k in url.lower() for k in ['emi', 'calculator', 'calculate', 'loan-calculator']):
                if not any(k in url.lower() for k in ['terms', 'tnc', '.pdf', 'mitc', 'terms-and-conditions']):
                    logger.info(f"Skipping calculator/tool URL: {url}")
                    return None
            
            # Extract content
            if is_pdf:
                text = await self._extract_pdf(url)
            else:
                text = await self._extract_html(url)
            
            if not text or len(text) < 500:
                logger.info(f"   ⚠️ Insufficient content")
                return None
            
            # Check relevance
            is_relevant, relevance_score = semantic_filter.is_relevant(text, url)
            if not is_relevant:
                logger.info(f"   ❌ Not relevant (score: {relevance_score:.2f})")
                return None
            
            # Validate
            is_valid, validation_result = validator.validate(text, bank_name, loan_type)
            if not is_valid:
                logger.info(f"   ❌ Failed validation: {validation_result['reasons']}")
                return None
            
            # Calculate confidence
            confidence = validation_result['overall_score']

            # Heuristics to boost confidence
            url_lower = url.lower()
            if is_pdf:
                confidence += 0.12
            # URLs or paths that contain terms/tnc/mitc are strong signals
            if any(k in url_lower for k in ['terms', 'tnc', 'mitc', 'tncs', 'terms-and-conditions']):
                confidence += 0.12
            # Titles/snippets containing 'schedule of charges' or 'key facts' are valuable
            if any(k in text.lower() for k in ['schedule of charges', 'key facts', 'tariff']):
                confidence += 0.08

            # Slight bonus if from official domain
            if self._is_official_domain(url, bank_name):
                confidence += 0.06

            confidence = min(confidence, 1.0)
            
            logger.info(f"   ✅ VALID! Confidence: {confidence:.2%}")
            
            return {
                'url': url,
                'text': text[:50000],
                'text_length': len(text),
                'confidence': confidence,
                'metadata': {
                    'is_pdf': is_pdf,
                    'relevance_score': relevance_score,
                    'validation_scores': validation_result['scores']
                }
            }
            
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
            return None
    
    async def _fetch_bytes(self, url: str, timeout: int = 30) -> Optional[bytes]:
        """Download raw bytes from a URL with SSL fallback."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as response:
                    if response.status != 200:
                        return None
                    return await response.read()
        except Exception as exc:
            # Retry without SSL verification if certificate issues arise
            if 'CERTIFICATE_VERIFY_FAILED' in str(exc) or 'ssl' in str(exc).lower():
                logger.warning(f"SSL verify failed for {url}, retrying with ssl disabled")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=timeout * 2, ssl=False) as response:
                        if response.status != 200:
                            return None
                        return await response.read()
            logger.error(f"   ❌ Download failed: {exc}")
            return None

    async def _extract_pdf(self, url: str) -> Optional[str]:
        """Extract text from a PDF document."""
        pdf_content = await self._fetch_bytes(url, timeout=45)
        if not pdf_content:
            return None

        try:
            reader = PyPDF2.PdfReader(BytesIO(pdf_content))
        except Exception as exc:
            logger.error(f"   ❌ PDF parse failed: {exc}")
            return None

        text_chunks: List[str] = []
        for page in reader.pages[:100]:  # Hard cap to avoid massive PDFs
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text:
                text_chunks.append(page_text)

        text = "\n".join(text_chunks).strip()
        logger.info(f"   📄 PDF: {len(text)} chars")
        return text or None

    async def _extract_html(self, url: str) -> Optional[str]:
        """Extract and clean text content from an HTML page."""
        raw_bytes = await self._fetch_bytes(url, timeout=30)
        if not raw_bytes:
            return None

        # Attempt multiple decodings before falling back to latin-1
        text_candidate: Optional[str] = None
        for encoding in ('utf-8', 'utf-16', 'iso-8859-1', 'windows-1252'):
            try:
                text_candidate = raw_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text_candidate is None:
            text_candidate = raw_bytes.decode('latin-1', errors='replace')

        soup = BeautifulSoup(text_candidate, 'html.parser')

        # Remove noisy elements that never contain loan terms
        for element in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript']):
            element.decompose()

        text = soup.get_text(separator='\n')
        text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())
        logger.info(f"   🌐 HTML: {len(text)} chars")
        return text or None
    
    def _is_official_domain(self, url: str, bank_name: str) -> bool:
        """Check if URL is from official bank domain"""
        domain = urlparse(url).netloc.lower()
        trusted = self.search.DOMAIN_MAP.get(bank_name, '')
        return trusted in domain if trusted else False

# ==============================================================================
# EASY INTERFACE (Compatible with existing code)
# ==============================================================================

class EasyScraper:
    """Drop-in replacement for your existing EasyScraper"""
    
    def __init__(self, google_api_key: str = None, google_cse_id: str = None, test_mode: bool = False):
        # STEP 27: Support test mode
        self.scraper = FixedBankScraper(
            google_api_key or "YOUR_API_KEY_HERE",
            google_cse_id or "YOUR_CSE_ID_HERE",
            test_mode=test_mode
        )
        # STEP 29: Cache access (use a simple proxy class)
        self.cache = self.scraper
        
        logger.info("🚀 EasyScraper initialized with fixes")
        if test_mode:
            logger.info("🧪 Test mode enabled")
    
    def scrape_bank(
        self,
        bank_name: str,
        loan_type: str = "personal",
        save_results: bool = False,
        use_cache: bool = True  # STEP 29: Add cache parameter
    ) -> Dict:
        """Scrape a single bank"""
        result = asyncio.run(self.scraper.scrape_bank(bank_name, loan_type, use_cache=use_cache))
        
        if save_results and result['success']:
            self._save_results(result)
        
        return result
    
    def scrape_multiple_banks(
        self,
        banks: List[str],
        loan_type: str = "personal"
    ) -> Dict[str, Dict]:
        """Scrape multiple banks"""
        async def scrape_all():
            tasks = [self.scraper.scrape_bank(bank, loan_type) for bank in banks]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            batch_results = {}
            for bank, result in zip(banks, results):
                if isinstance(result, Exception):
                    batch_results[bank] = {'success': False, 'error': str(result)}
                else:
                    batch_results[bank] = result
            
            return batch_results
        
        return asyncio.run(scrape_all())
    
    def _save_results(self, result: Dict):
        """Save results to disk"""
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f"{result['bank']}_{result['loan_type']}_loan_{timestamp}.json"
        filepath = output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        logger.info(f"💾 Saved: {filepath}")

# ==============================================================================
# CLI (Optional)
# ==============================================================================

def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description="Fixed Bank Document Scraper")
    parser.add_argument('--bank', type=str, required=True)
    parser.add_argument('--loan-type', type=str, default='personal')
    parser.add_argument('--api-key', type=str, required=True)
    parser.add_argument('--cse-id', type=str, required=True)
    
    args = parser.parse_args()
    
    scraper = EasyScraper(args.api_key, args.cse_id)
    result = scraper.scrape_bank(args.bank, args.loan_type, save_results=True)
    
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
