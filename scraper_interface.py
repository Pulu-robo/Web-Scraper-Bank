"""
STRICT & ACCURATE Bank Document Scraper
========================================
Fixes:
1. Strict bank name validation (prevents cross-bank contamination)
2. Strict loan type matching (prevents wrong loan types)
3. More aggressive filtering
4. Better URL validation
5. Stricter confidence thresholds
"""

import asyncio
import hashlib
import logging
import time
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse
import json

import aiohttp
from bs4 import BeautifulSoup
import PyPDF2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ==============================================================================
# STRICT BANK VALIDATOR
# ==============================================================================

class StrictBankValidator:
    """Ensures documents are ACTUALLY from the correct bank"""
    
    BANK_IDENTIFIERS = {
        'HDFC': {
            'domains': ['hdfcbank.com'],
            'names': ['hdfc', 'hdfc bank', 'housing development finance'],
            'exclude_names': ['axis', 'icici', 'sbi', 'kotak', 'yes bank', 'indusind']
        },
        'SBI': {
            'domains': ['sbi.co.in', 'onlinesbi.sbi', 'bank.sbi'],
            'names': ['sbi', 'state bank of india', 'state bank'],
            'exclude_names': ['hdfc', 'icici', 'axis', 'kotak']
        },
        'ICICI': {
            'domains': ['icicibank.com'],
            'names': ['icici', 'icici bank'],
            'exclude_names': ['hdfc', 'axis', 'sbi', 'kotak', 'yes bank']
        },
        'Axis': {
            'domains': ['axisbank.com'],
            'names': ['axis', 'axis bank'],
            'exclude_names': ['hdfc', 'icici', 'sbi', 'kotak']
        },
        'Kotak': {
            'domains': ['kotak.com', 'kotakbank.com'],
            'names': ['kotak', 'kotak mahindra'],
            'exclude_names': ['hdfc', 'icici', 'axis', 'sbi']
        },
        'Yes Bank': {
            'domains': ['yesbank.in'],
            'names': ['yes bank', 'yesbank'],
            'exclude_names': ['hdfc', 'icici', 'axis', 'sbi', 'kotak']
        },
        'IndusInd': {
            'domains': ['indusind.com'],
            'names': ['indusind', 'indusind bank'],
            'exclude_names': ['hdfc', 'icici', 'axis', 'sbi']
        },
        'IDFC First': {
            'domains': ['idfcfirstbank.com'],
            'names': ['idfc', 'idfc first', 'idfc first bank'],
            'exclude_names': ['hdfc', 'icici', 'axis', 'sbi']
        }
    }
    
    @staticmethod
    def validate_bank(text: str, url: str, bank_name: str) -> Tuple[bool, str]:
        """
        STRICT validation - ensures document is from correct bank ONLY
        Returns: (is_valid, reason)
        """
        text_lower = text.lower()
        url_lower = url.lower()
        
        bank_info = StrictBankValidator.BANK_IDENTIFIERS.get(bank_name)
        if not bank_info:
            return False, f"Unknown bank: {bank_name}"
        
        # STEP 1: Check URL domain (MOST RELIABLE)
        url_domain = urlparse(url).netloc.lower()
        is_correct_domain = any(domain in url_domain for domain in bank_info['domains'])
        
        if not is_correct_domain:
            return False, f"Wrong domain: {url_domain} (expected: {bank_info['domains']})"
        
        # STEP 2: Check for WRONG bank names (CRITICAL)
        exclude_names = bank_info['exclude_names']
        text_sample = text_lower[:3000]  # Check first 3000 chars
        
        for wrong_bank in exclude_names:
            # Look for wrong bank name mentions
            if wrong_bank in text_sample:
                # But allow in comparison tables (e.g., "compare with HDFC")
                wrong_count = text_sample.count(wrong_bank)
                if wrong_count > 2:  # More than 2 mentions = probably wrong bank
                    return False, f"Contains wrong bank name '{wrong_bank}' ({wrong_count} times)"
        
        # STEP 3: Check for CORRECT bank name
        correct_names = bank_info['names']
        correct_name_found = any(name in text_lower[:2000] for name in correct_names)
        
        if not correct_name_found:
            return False, f"Correct bank name not found in document"
        
        logger.info(f"✅ Bank validation PASSED: {bank_name}")
        return True, "Valid bank document"

# ==============================================================================
# STRICT LOAN TYPE VALIDATOR
# ==============================================================================

class StrictLoanTypeValidator:
    """Ensures documents match the EXACT loan type requested"""
    
    LOAN_TYPE_KEYWORDS = {
        'personal': {
            'required': ['personal loan', 'personal credit'],
            'allowed': ['personal', 'unsecured loan', 'consumer loan'],
            'forbidden': ['home loan', 'housing loan', 'mortgage', 
                         'car loan', 'auto loan', 'vehicle loan',
                         'education loan', 'student loan',
                         'business loan', 'commercial loan']
        },
        'home': {
            'required': ['home loan', 'housing loan'],
            'allowed': ['home', 'housing', 'mortgage', 'property loan'],
            'forbidden': ['personal loan', 'car loan', 'auto loan',
                         'education loan', 'business loan']
        },
        'auto': {
            'required': ['car loan', 'auto loan', 'vehicle loan'],
            'allowed': ['auto', 'car', 'vehicle', 'automobile'],
            'forbidden': ['personal loan', 'home loan', 'housing loan',
                         'education loan', 'business loan']
        },
        'education': {
            'required': ['education loan', 'student loan'],
            'allowed': ['education', 'student', 'study loan'],
            'forbidden': ['personal loan', 'home loan', 'car loan',
                         'business loan']
        },
        'business': {
            'required': ['business loan', 'commercial loan'],
            'allowed': ['business', 'commercial', 'enterprise'],
            'forbidden': ['personal loan', 'home loan', 'car loan',
                         'education loan']
        }
    }
    
    @staticmethod
    def validate_loan_type(text: str, url: str, loan_type: str) -> Tuple[bool, str]:
        """
        STRICT loan type validation
        Returns: (is_valid, reason)
        """
        text_lower = text.lower()
        url_lower = url.lower()
        
        loan_info = StrictLoanTypeValidator.LOAN_TYPE_KEYWORDS.get(loan_type.lower())
        if not loan_info:
            return False, f"Unknown loan type: {loan_type}"
        
        combined_text = url_lower + " " + text_lower[:3000]
        
        # STEP 1: Check for FORBIDDEN loan types (CRITICAL)
        forbidden = loan_info['forbidden']
        for forbidden_type in forbidden:
            if forbidden_type in combined_text:
                count = combined_text.count(forbidden_type)
                if count > 1:  # More than 1 mention = wrong loan type
                    return False, f"Contains wrong loan type '{forbidden_type}' ({count} times)"
        
        # STEP 2: Must contain REQUIRED keywords
        required = loan_info['required']
        has_required = any(req in combined_text for req in required)
        
        if not has_required:
            # Check allowed keywords as fallback
            allowed = loan_info['allowed']
            has_allowed = sum(1 for allow in allowed if allow in combined_text)
            
            if has_allowed < 2:  # Need at least 2 allowed keywords
                return False, f"Missing required keywords: {required}"
        
        logger.info(f"✅ Loan type validation PASSED: {loan_type}")
        return True, f"Valid {loan_type} loan document"

# ==============================================================================
# STRICT SEMANTIC FILTER
# ==============================================================================

class StrictSemanticFilter:
    """More conservative semantic filtering"""
    
    def __init__(self, bank_name: str, loan_type: str):
        self.bank_name = bank_name
        self.loan_type = loan_type.lower()
        self.bank_validator = StrictBankValidator()
        self.loan_validator = StrictLoanTypeValidator()
        logger.info(f"🔒 Strict filter: {bank_name} {loan_type} loans")
    
    def is_relevant(self, text: str, url: str, metadata: Dict = None) -> Tuple[bool, float, str]:
        """
        Strict relevance check with detailed reason
        Returns: (is_relevant, score, reason)
        """
        if not text or len(text) < 200:
            return False, 0.0, "Content too short"
        
        text_lower = text.lower()
        url_lower = url.lower()
        
        # CRITICAL: Negative filtering FIRST
        negative_keywords = [
            'deceased', 'death', 'demise', 'claim settlement',
            'nominee', 'legal heir', 'succession',
            'credit card', 'debit card', 'atm',
            'savings account', 'current account', 'deposit',
            'complaint', 'grievance', 'feedback',
            'career', 'job', 'branch', 'ifsc'
        ]
        
        for neg_kw in negative_keywords:
            if neg_kw in url_lower or neg_kw in text_lower[:800]:
                return False, 0.0, f"Contains negative keyword: {neg_kw}"
        
        # STEP 1: Validate BANK (CRITICAL)
        is_valid_bank, bank_reason = self.bank_validator.validate_bank(text, url, self.bank_name)
        if not is_valid_bank:
            return False, 0.0, f"Bank validation failed: {bank_reason}"
        
        # STEP 2: Validate LOAN TYPE (CRITICAL)
        is_valid_loan, loan_reason = self.loan_validator.validate_loan_type(text, url, self.loan_type)
        if not is_valid_loan:
            return False, 0.0, f"Loan type validation failed: {loan_reason}"
        
        # STEP 3: Calculate relevance score
        score_parts = {}
        
        # Bank mentions (20%)
        bank_count = text_lower[:2000].count(self.bank_name.lower())
        score_parts['bank'] = min(bank_count / 3.0, 1.0) * 0.20
        
        # Loan type mentions (25%)
        loan_count = text_lower[:2000].count(self.loan_type)
        score_parts['loan_type'] = min(loan_count / 2.0, 1.0) * 0.25
        
        # Terms keywords (30%)
        terms_kw = ['terms and conditions', 'terms & conditions', 'mitc', 'agreement']
        terms_score = sum(1 for kw in terms_kw if kw in text_lower[:2000])
        score_parts['terms'] = min(terms_score / 2.0, 1.0) * 0.30
        
        # Financial keywords (25%)
        financial_kw = ['interest rate', 'processing fee', 'charges', 'emi', 'tenure']
        financial_score = sum(1 for kw in financial_kw if kw in text_lower)
        score_parts['financial'] = min(financial_score / 3.0, 1.0) * 0.25
        
        final_score = sum(score_parts.values())
        
        # STRICTER threshold: 0.40 (was 0.25)
        is_relevant = final_score >= 0.40
        
        reason = f"Score: {final_score:.2f} (bank={score_parts['bank']:.2f}, loan={score_parts['loan_type']:.2f})"
        
        if is_relevant:
            logger.info(f"✅ RELEVANT ({final_score:.2f}): {url[:70]}")
        else:
            logger.info(f"❌ Not relevant ({final_score:.2f}): {url[:70]}")
        
        return is_relevant, final_score, reason

# ==============================================================================
# STRICT VALIDATOR
# ==============================================================================

class StrictValidator:
    """Comprehensive strict validation"""
    
    @staticmethod
    def validate(
        text: str,
        bank_name: str,
        loan_type: str,
        url: str = ""
    ) -> Tuple[bool, Dict]:
        """
        Multi-level strict validation
        """
        result = {
            'valid': False,
            'reasons': [],
            'scores': {},
            'overall_score': 0.0
        }
        
        if not text or len(text) < 800:  # Increased from 500
            result['reasons'].append("Content too short (min 800 chars)")
            return False, result
        
        text_lower = text.lower()
        
        # LEVEL 1: Negative check (MUST PASS)
        negative_terms = [
            'deceased', 'death claim', 'settlement claim',
            'nominee', 'legal heir', 'credit card application'
        ]
        
        for term in negative_terms:
            if term in text_lower[:1500]:
                result['reasons'].append(f"REJECTED: Contains '{term}'")
                return False, result
        
        result['scores']['negative_check'] = 1.0
        
        # LEVEL 2: Bank name MUST be present multiple times
        bank_clean = bank_name.lower().replace(' ', '').replace('-', '')
        text_clean = text_lower.replace(' ', '').replace('-', '')
        
        bank_mentions = text_clean[:3000].count(bank_clean)
        result['scores']['bank_name'] = min(bank_mentions / 3.0, 1.0)
        
        if bank_mentions < 2:  # Must appear at least 2 times
            result['reasons'].append(f"Bank name appears only {bank_mentions} times (need 2+)")
            # Don't fail yet, but penalize heavily
        
        # LEVEL 3: Loan type MUST be clear
        loan_variations = [
            f'{loan_type} loan',
            f'{loan_type}loan',
            loan_type
        ]
        
        loan_mentions = sum(text_lower[:2500].count(var) for var in loan_variations)
        result['scores']['loan_type'] = min(loan_mentions / 2.0, 1.0)
        
        if loan_mentions < 1:
            result['reasons'].append(f"Loan type '{loan_type}' not found")
        
        # LEVEL 4: Essential financial keywords (STRICT)
        essential_kw = [
            'interest rate', 'processing fee', 'charges',
            'tenure', 'emi', 'loan amount', 'repayment'
        ]
        essential_count = sum(1 for kw in essential_kw if kw in text_lower)
        result['scores']['essential'] = min(essential_count / 4.0, 1.0)
        
        if essential_count < 3:  # Need at least 3
            result['reasons'].append(f"Only {essential_count}/7 essential keywords")
        
        # LEVEL 5: Terms & conditions indicators
        terms_kw = ['terms and conditions', 'agreement', 'conditions apply']
        terms_count = sum(1 for kw in terms_kw if kw in text_lower)
        result['scores']['terms'] = min(terms_count / 2.0, 1.0)
        
        # LEVEL 6: Document length (good docs are comprehensive)
        length_score = min(len(text) / 15000, 1.0)  # 15k chars for full score
        result['scores']['length'] = length_score
        
        # Calculate weighted score with STRICTER weights
        overall = (
            result['scores']['negative_check'] * 0.25 +  # Must pass
            result['scores']['bank_name'] * 0.25 +       # Critical
            result['scores']['loan_type'] * 0.20 +       # Critical
            result['scores']['essential'] * 0.20 +       # Important
            result['scores']['terms'] * 0.10
        )
        
        result['overall_score'] = overall
        
        # STRICTER threshold: 0.70 (was 0.60)
        result['valid'] = overall >= 0.70
        
        if result['valid']:
            result['reasons'].append(f"✅ PASSED validation (score: {overall:.2f})")
            logger.info(f"✅ Validation PASSED: {overall:.2f}")
        else:
            result['reasons'].append(f"❌ FAILED validation (score: {overall:.2f}, need 0.70)")
            logger.info(f"❌ Validation FAILED: {overall:.2f}")
        
        return result['valid'], result

# ==============================================================================
# STRICT URL FILTER
# ==============================================================================

class StrictURLFilter:
    """Aggressive URL filtering"""
    
    @staticmethod
    def filter_urls(urls: List[str], bank_name: str, loan_type: str) -> List[str]:
        """Filter URLs with strict criteria"""
        bank_domains = {
            'HDFC': 'hdfcbank.com',
            'SBI': 'sbi.co.in',
            'ICICI': 'icicibank.com',
            'Axis': 'axisbank.com',
            'Kotak': 'kotak.com',
            'Yes Bank': 'yesbank.in',
            'IndusInd': 'indusind.com',
            'IDFC First': 'idfcfirstbank.com'
        }
        
        expected_domain = bank_domains.get(bank_name, '')
        
        # Negative patterns
        exclude = [
            'deceased', 'claim', 'settlement', 'death',
            'credit-card', 'debit-card', 'creditcard',
            'savings', 'deposit', 'account-opening',
            'complaint', 'grievance', 'feedback',
            'career', 'job', 'about', 'news', 'branch', 'atm'
        ]
        
        # Positive patterns for loan type
        loan_patterns = {
            'personal': ['personal', 'personal-loan'],
            'home': ['home', 'housing', 'home-loan', 'housing-loan'],
            'auto': ['car', 'auto', 'vehicle', 'car-loan', 'auto-loan'],
            'education': ['education', 'student', 'education-loan'],
            'business': ['business', 'commercial', 'business-loan']
        }
        
        required_patterns = loan_patterns.get(loan_type.lower(), [loan_type.lower()])
        
        filtered = []
        for url in urls:
            url_lower = url.lower()
            domain = urlparse(url).netloc.lower()
            
            # MUST be from correct domain
            if expected_domain not in domain:
                logger.info(f"🚫 Wrong domain: {domain} (expected {expected_domain})")
                continue
            
            # Must NOT contain negative patterns
            if any(ex in url_lower for ex in exclude):
                logger.info(f"🚫 Contains exclude pattern: {url[:70]}")
                continue
            
            # MUST contain loan type indicator
            has_loan_pattern = any(pattern in url_lower for pattern in required_patterns)
            if not has_loan_pattern:
                logger.info(f"🚫 Missing loan type in URL: {url[:70]}")
                continue
            
            # Prefer terms/mitc/charges URLs
            has_good_keywords = any(kw in url_lower for kw in ['terms', 'mitc', 'charges', 'agreement', 'pdf'])
            
            if has_good_keywords or url_lower.endswith('.pdf'):
                filtered.append(url)
                logger.info(f"✅ Good URL: {url[:70]}")
        
        return filtered

# ==============================================================================
# MAIN STRICT SCRAPER
# ==============================================================================

class StrictBankScraper:
    """Strict scraper that prevents cross-contamination"""
    
    def __init__(self, google_api_key: str, google_cse_id: str):
        self.api_key = google_api_key
        self.cse_id = google_cse_id
        self.url_filter = StrictURLFilter()
        logger.info("🔒 Strict Bank Scraper initialized")
    
    async def scrape_bank(self, bank_name: str, loan_type: str) -> Dict:
        """Scrape with strict validation"""
        start_time = time.time()
        
        logger.info(f"\n{'='*70}")
        logger.info(f"🔒 STRICT SCRAPER: {bank_name} {loan_type} loan")
        logger.info(f"{'='*70}\n")
        
        result = {
            'bank': bank_name,
            'loan_type': loan_type,
            'success': False,
            'documents': [],
            'metadata': {
                'filtered_out': 0,
                'validation_failures': 0,
                'execution_time_seconds': 0,
                'errors': []
            }
        }
        
        try:
            # Initialize strict filters
            semantic_filter = StrictSemanticFilter(bank_name, loan_type)
            validator = StrictValidator()
            
            # Get URLs
            urls = await self._get_urls(bank_name, loan_type)
            
            if not urls:
                result['metadata']['errors'].append("No URLs found")
                return result
            
            logger.info(f"📊 Processing {len(urls)} URLs\n")
            
            # Process each URL
            for url in urls:
                doc = await self._process_url(
                    url, bank_name, loan_type,
                    semantic_filter, validator
                )
                
                if doc:
                    result['documents'].append(doc)
                else:
                    result['metadata']['filtered_out'] += 1
            
            # Sort by confidence
            result['documents'].sort(key=lambda x: x['confidence'], reverse=True)
            
            # Success if we have ANY high-confidence doc
            if result['documents'] and result['documents'][0]['confidence'] > 0.70:
                result['success'] = True
            
            result['metadata']['execution_time_seconds'] = round(time.time() - start_time, 2)
            
            logger.info(f"\n{'='*70}")
            logger.info(f"✅ STRICT SCRAPE COMPLETE")
            logger.info(f"📄 Valid documents: {len(result['documents'])}")
            logger.info(f"🚫 Filtered out: {result['metadata']['filtered_out']}")
            if result['documents']:
                logger.info(f"💪 Best confidence: {result['documents'][0]['confidence']:.2%}")
            logger.info(f"⏱️ Time: {result['metadata']['execution_time_seconds']}s")
            logger.info(f"{'='*70}\n")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Fatal error: {e}", exc_info=True)
            result['metadata']['errors'].append(f"Fatal: {str(e)}")
            return result
    
    async def _get_urls(self, bank_name: str, loan_type: str) -> List[str]:
        """Get and filter URLs strictly"""
        urls = []
        
        # Try API
        if self.api_key and self.api_key != "YOUR_API_KEY_HERE":
            queries = self._generate_queries(bank_name, loan_type)
            
            for query in queries[:2]:
                try:
                    search_urls = await self._search_google(query)
                    urls.extend(search_urls)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Search error: {e}")
        
        # Add fallback
        fallback = self._get_fallback_urls(bank_name, loan_type)
        urls.extend(fallback)
        
        # STRICT filtering
        filtered_urls = self.url_filter.filter_urls(list(set(urls)), bank_name, loan_type)
        
        return filtered_urls[:6]  # Max 6 URLs
    
    def _generate_queries(self, bank_name: str, loan_type: str) -> List[str]:
        """Generate very specific queries"""
        domain_map = {
            'HDFC': 'hdfcbank.com',
            'SBI': 'sbi.co.in',
            'ICICI': 'icicibank.com',
            'Axis': 'axisbank.com',
            'Kotak': 'kotak.com',
            'Yes Bank': 'yesbank.in',
            'IndusInd': 'indusind.com',
            'IDFC First': 'idfcfirstbank.com'
        }
        
        domain = domain_map.get(bank_name, '')
        
        return [
            f'site:{domain} "{loan_type} loan" terms conditions filetype:pdf',
            f'site:{domain} "{loan_type} loan" MITC charges'
        ]
    
    async def _search_google(self, query: str) -> List[str]:
        """Search Google"""
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
                        return [item['link'] for item in data.get('items', [])]
                    elif response.status == 429:
                        logger.warning("⚠️ API rate limit")
                        return []
        except:
            return []
        
        return []
    
    def _get_fallback_urls(self, bank_name: str, loan_type: str) -> List[str]:
        """Loan-type specific fallback URLs"""
        fallback_db = {
            'HDFC': {
                'personal': ['https://www.hdfcbank.com/personal/borrow/popular-loans/personal-loan'],
                'home': ['https://www.hdfcbank.com/personal/borrow/home-loan'],
                'auto': ['https://www.hdfcbank.com/personal/borrow/popular-loans/car-loan']
            },
            'SBI': {
                'personal': ['https://sbi.co.in/web/personal-banking/loans/personal-loan'],
                'home': ['https://sbi.co.in/web/personal-banking/loans/home-loans']
            },
            'ICICI': {
                'personal': ['https://www.icicibank.com/Personal-Banking/loans/personal-loan'],
                'home': ['https://www.icicibank.com/Personal-Banking/loans/home-loan']
            },
            'Axis': {
                'personal': ['https://www.axisbank.com/retail/loans/personal-loan'],
                'home': ['https://www.axisbank.com/retail/loans/home-loan']
            },
            'Kotak': {
                'personal': ['https://www.kotak.com/en/personal-banking/loans/personal-loan.html']
            }
        }
        
        return fallback_db.get(bank_name, {}).get(loan_type, [])
    
    async def _process_url(
        self,
        url: str,
        bank_name: str,
        loan_type: str,
        semantic_filter,
        validator
    ) -> Optional[Dict]:
        """Process URL with strict checks"""
        try:
            logger.info(f"🔄 Processing: {url[:70]}")
            
            # Extract
            text = None
            is_pdf = url.lower().endswith('.pdf')
            
            if is_pdf:
                text = await self._extract_pdf(url)
            else:
                text = await self._extract_html(url)
            
            if not text or len(text) < 800:
                logger.info(f"   ⚠️ Content too short")
                return None
            
            # Strict relevance check
            is_relevant, relevance_score, reason = semantic_filter.is_relevant(text, url, {'is_pdf': is_pdf})
            
            if not is_relevant:
                logger.info(f"   ❌ {reason}")
                return None
            
            # Strict validation
            is_valid, validation_result = validator.validate(text, bank_name, loan_type, url)
            
            if not is_valid:
                logger.info(f"   ❌ Validation failed: {validation_result['reasons']}")
                return None
            
            # Calculate confidence
            confidence = validation_result['overall_score'] * 0.7 + relevance_score * 0.3
            
            if is_pdf:
                confidence += 0.05
            
            confidence = min(confidence, 0.95)
            
            logger.info(f"   ✅ VALID! Confidence: {confidence:.2%}")
            
            return {
                'url': url,
                'text': text[:50000],
                'text_length': len(text),
                'confidence': confidence,
                'metadata': {
                    'is_pdf': is_pdf,
                    'relevance_score': relevance_score,
                    'validation_score': validation_result['overall_score']
                }
            }
            
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
            return None
    
    async def _extract_pdf(self, url: str) -> Optional[str]:
        """Extract PDF"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        return None
                    
                    pdf_content = await response.read()
                    reader = PyPDF2.PdfReader(BytesIO(pdf_content))
                    
                    text = ""
                    for page in reader.pages[:100]:
                        text += page.extract_text() + "\n"
                    
                    logger.info(f"   📄 PDF: {len(text)} chars")
                    return text
        except Exception as e:
            logger.error(f"   ❌ PDF extraction failed: {e}")
            return None
    
    async def _extract_html(self, url: str) -> Optional[str]:
        """Extract HTML"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=20, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }) as response:
                    if response.status != 200:
                        return None
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Remove noise
                    for element in soup(['script', 'style', 'nav', 'footer', 'header']):
                        element.decompose()
                    
                    text = soup.get_text(separator=' ', strip=True)
                    text = ' '.join(text.split())
                    
                    logger.info(f"   🌐 HTML: {len(text)} chars")
                    return text
        except Exception as e:
            logger.error(f"   ❌ HTML extraction failed: {e}")
            return None

# ==============================================================================
# EASY INTERFACE
# ==============================================================================

class EasyScraper:
    """Drop-in replacement with strict validation"""
    
    def __init__(self, google_api_key: str = None, google_cse_id: str = None):
        self.scraper = StrictBankScraper(
            google_api_key or "YOUR_API_KEY_HERE",
            google_cse_id or "YOUR_CSE_ID_HERE"
        )
        logger.info("🔒 Strict EasyScraper initialized")
    
    def scrape_bank(
        self,
        bank_name: str,
        loan_type: str = "personal",
        save_results: bool = False
    ) -> Dict:
        """Scrape with strict validation"""
        result = asyncio.run(self.scraper.scrape_bank(bank_name, loan_type))
        
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
        """Save results"""
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        
        # Save summary
        json_file = output_dir / f"{result['bank']}_{result['loan_type']}_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        # Save full documents
        for i, doc in enumerate(result.get('documents', []), 1):
            txt_file = output_dir / f"{result['bank']}_{result['loan_type']}_doc{i}_{timestamp}.txt"
            with open(txt_file, 'w', encoding='utf-8') as f:
                f.write(f"Bank: {result['bank']}\n")
                f.write(f"Loan Type: {result['loan_type']}\n")
                f.write(f"URL: {doc['url']}\n")
                f.write(f"Confidence: {doc['confidence']:.2%}\n")
                f.write(f"{'='*70}\n\n")
                f.write(doc['text'])
        
        logger.info(f"💾 Results saved to: {output_dir}")

# ==============================================================================
# USAGE
# ==============================================================================

if __name__ == "__main__":
    scraper = EasyScraper(
        google_api_key="YOUR_API_KEY",
        google_cse_id="YOUR_CSE_ID"
    )
    
    # Test
    result = scraper.scrape_bank('HDFC', 'personal')
    
    print(f"\n{'='*70}")
    print(f"Bank: {result['bank']}")
    print(f"Loan Type: {result['loan_type']}")
    print(f"Success: {result['success']}")
    print(f"Documents: {len(result['documents'])}")
    print(f"Filtered Out: {result['metadata']['filtered_out']}")
    if result['documents']:
        print(f"Best Confidence: {result['documents'][0]['confidence']:.2%}")
        print(f"Best URL: {result['documents'][0]['url']}")
    print(f"{'='*70}\n")
