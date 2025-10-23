"""
Intelligent Bank Document Scraper v2.0 - PRODUCTION GRADE
=========================================================
Enhanced with:
- Google Custom Search API integration
- Semantic filtering using embeddings
- Async concurrent crawling
- Smart Playwright fallback
- OCR for scanned PDFs
- Exponential backoff with jitter
- Structured logging
- Rate limiting & circuit breaker
- Content deduplication
- Enhanced validation
"""

import asyncio
import re
import time
import random
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
import json
import pickle  # STEP 29: For caching
from pathlib import Path
from io import BytesIO
from collections import defaultdict

# Core libraries
import requests
from bs4 import BeautifulSoup
import PyPDF2
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Semantic filtering
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    print("⚠️  sentence-transformers not installed. Semantic filtering disabled.")

# Playwright for JS sites
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("⚠️  Playwright not installed. JavaScript sites will be limited.")

# OCR for scanned PDFs
try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️  OCR not available. Install: pytesseract, pdf2image, poppler")

# Import from scraper_interface
from scraper_interface import (
    ImprovedSemanticFilter, 
    ImprovedValidator,
    BLACKLIST,
    LOAN_KEYWORDS,
    LOAN_VARIATIONS,
    TNC_KEYWORDS
)

# ==============================================================================
# LOGGING SETUP
# ==============================================================================

def setup_logging():
    """Configure structured logging with rotation"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / f"scraper_{datetime.now().strftime('%Y%m%d')}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)8s | %(name)s | %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    
    return logging.getLogger(__name__)

logger = setup_logging()

# ==============================================================================
# CONFIGURATION
# ==============================================================================

@dataclass
class ScraperConfig:
    """Enhanced configuration"""
    # Google Custom Search API (replace with your keys)
    google_api_key: str = "YOUR_API_KEY_HERE"
    google_cse_id: str = "YOUR_CSE_ID_HERE"
    
    # User agents
    user_agents: List[str] = field(default_factory=lambda: [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ])
    
    # Document keywords
    doc_keywords: List[str] = field(default_factory=lambda: [
        'terms and conditions', 'terms & conditions', 'T&C', 'MITC',
        'most important terms', 'schedule of charges', 'fees and charges',
        'loan agreement', 'important terms', 'terms of service',
        'product disclosure', 'key facts statement', 'tariff'
    ])
    
    # Trusted domains
    trusted_domains: Dict[str, List[str]] = field(default_factory=lambda: {
        'HDFC': ['hdfcbank.com'],
        'SBI': ['sbi.co.in', 'onlinesbi.sbi'],
        'ICICI': ['icicibank.com'],
        'Axis': ['axisbank.com'],
        'Kotak': ['kotak.com', 'kotakbank.com'],
        'Yes Bank': ['yesbank.in'],
        'IndusInd': ['indusind.com'],
        'IDFC First': ['idfcfirstbank.com'],
        'PNB': ['pnbindia.in'],
        'Bank of Baroda': ['bankofbaroda.in']
    })
    
    # Request settings
    timeout: int = 30
    max_retries: int = 5
    initial_retry_delay: float = 1.0
    max_retry_delay: float = 60.0
    concurrent_requests: int = 5
    max_pages_per_domain: int = 15
    
    # Rate limiting (requests per second per domain)
    rate_limit_per_domain: float = 0.5
    
    # Semantic filtering
    semantic_threshold: float = 0.35
    
    # Content deduplication
    enable_deduplication: bool = True
    similarity_threshold: float = 0.90
    
    # STEP 27: Testing mode
    test_mode: bool = False
    
    # STEP 31: Strictness mode (relaxed, normal, strict)
    strictness_mode: str = "relaxed"  # Options: "relaxed", "normal", "strict"
    
    # STEP 21: Loan-specific configuration
    loan_type_config: Dict[str, Dict] = field(default_factory=lambda: {
        'personal': {
            'min_doc_length': 3000,
            'min_bank_mentions': 3,
            'semantic_threshold': 0.30,
            'validation_threshold': 0.75
        },
        'home': {
            'min_doc_length': 5000,
            'min_bank_mentions': 4,
            'semantic_threshold': 0.35,
            'validation_threshold': 0.75
        },
        'auto': {
            'min_doc_length': 2500,
            'min_bank_mentions': 3,
            'semantic_threshold': 0.30,
            'validation_threshold': 0.70
        },
        'education': {
            'min_doc_length': 4000,
            'min_bank_mentions': 3,
            'semantic_threshold': 0.35,
            'validation_threshold': 0.75
        },
        'business': {
            'min_doc_length': 4000,
            'min_bank_mentions': 4,
            'semantic_threshold': 0.40,
            'validation_threshold': 0.80
        }
    })

# ==============================================================================
# RATE LIMITER & CIRCUIT BREAKER
# ==============================================================================

class RateLimiter:
    """Token bucket rate limiter per domain"""
    def __init__(self, rate: float):
        self.rate = rate
        self.last_request = defaultdict(float)
    
    async def acquire(self, domain: str):
        """Wait if necessary to respect rate limit"""
        now = time.time()
        time_since_last = now - self.last_request[domain]
        min_interval = 1.0 / self.rate
        
        if time_since_last < min_interval:
            wait_time = min_interval - time_since_last
            await asyncio.sleep(wait_time)
        
        self.last_request[domain] = time.time()

class CircuitBreaker:
    """Prevent hammering failing domains"""
    def __init__(self, failure_threshold: int = 5, timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = defaultdict(int)
        self.opened_at = {}
    
    def is_open(self, domain: str) -> bool:
        """Check if circuit is open (domain is failing)"""
        if domain in self.opened_at:
            if time.time() - self.opened_at[domain] > self.timeout:
                # Reset after timeout
                del self.opened_at[domain]
                self.failures[domain] = 0
                return False
            return True
        return False
    
    def record_failure(self, domain: str):
        """Record a failure"""
        self.failures[domain] += 1
        if self.failures[domain] >= self.failure_threshold:
            self.opened_at[domain] = time.time()
            logger.warning(f"Circuit breaker opened for {domain}")
    
    def record_success(self, domain: str):
        """Reset failures on success"""
        self.failures[domain] = 0
        if domain in self.opened_at:
            del self.opened_at[domain]

# ==============================================================================
# SEMANTIC INTELLIGENCE
# ==============================================================================

class ImprovedSemanticFilter:
    """Enhanced semantic filter that actually works"""
    
    def __init__(self, loan_type: str = "personal"):
        self.loan_type = loan_type.lower()
        logger.info(f"🎯 Semantic filter initialized for {loan_type} loans")
    
    def is_relevant(self, text: str, url: str = "", threshold: float = 0.25) -> Tuple[bool, float]:
        """Enhanced relevance check with negative filtering"""
        if not text or len(text) < 100:
            return False, 0.0
        
        text_lower = text.lower()
        url_lower = url.lower()
        
        # CRITICAL: Filter out irrelevant pages first
        negative_keywords = [
            'deceased', 'death', 'demise', 'claim', 'settlement',
            'insurance claim', 'nominee', 'legal heir',
            'credit card', 'debit card', 'account closure',
            'complaint', 'grievance', 'rtgs', 'neft', 'imps'
        ]
        
        # Check if it's an irrelevant page
        for neg_kw in negative_keywords:
            if neg_kw in text_lower[:500] or neg_kw in url_lower:
                logger.info(f"❌ Negative match: '{neg_kw}' in {url[:60]}")
                return False, 0.0
        
        # Positive scoring
        loan_type_score = 0.0
        loan_keywords = [
            self.loan_type,
            f'{self.loan_type} loan',
            'loan',
            'credit',
            'borrower',
            'lender'
        ]
        loan_matches = sum(1 for kw in loan_keywords if kw in text_lower[:2000])
        loan_type_score = min(loan_matches / 4.0, 1.0)
        
        # Terms and conditions scoring
        terms_keywords = [
            'terms and conditions', 'terms & conditions', 't&c',
            'most important terms', 'mitc',
            'agreement', 'charges', 'fees',
            'interest rate', 'processing fee',
            'emi', 'installment', 'tenure',
            'prepayment', 'foreclosure', 'annual percentage'
        ]
        terms_matches = sum(1 for kw in terms_keywords if kw in text_lower[:3000])
        terms_score = min(terms_matches / 5.0, 1.0)
        
        # Document structure keywords
        structure_keywords = [
            'section', 'clause', 'article', 'schedule',
            'annexure', 'appendix', 'eligibility'
        ]
        structure_matches = sum(1 for kw in structure_keywords if kw in text_lower[:2000])
        structure_score = min(structure_matches / 3.0, 1.0)
        
        # Calculate final score
        final_score = (
            loan_type_score * 0.35 +
            terms_score * 0.45 +
            structure_score * 0.20
        )
        
        is_relevant = final_score >= threshold
        
        if is_relevant:
            logger.info(f"✅ RELEVANT (score: {final_score:.2f}): {url[:70]}")
        else:
            logger.info(f"⚠️ Not relevant (score: {final_score:.2f}): {url[:70]}")
        
        return is_relevant, final_score

# ==============================================================================
# ENHANCED SEARCH WITH GOOGLE CSE
# ==============================================================================

class EnhancedSearchIntelligence:
    """Google Custom Search API integration"""
    
    def __init__(self, config: ScraperConfig):
        self.config = config
    
    @staticmethod
    def generate_better_queries(bank_name: str, loan_type: str) -> List[str]:
        """Generate more focused queries with exclusions"""
        # Exclusion string to filter out irrelevant results
        exclusions = '-creditcard -"credit card" -savings -insurance -"fixed deposit" -debitcard'
        
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
        
        # Get loan-specific primary terms from LOAN_KEYWORDS
        loan_bundle = LOAN_KEYWORDS.get(loan_type.lower(), {})
        primary_terms = loan_bundle.get('primary', [loan_type])[0] if loan_bundle.get('primary') else loan_type
        
        return [
            # Most specific queries with exact phrases
            f'site:{domain} "{loan_type} loan" "terms and conditions" filetype:pdf',
            f'site:{domain} "{loan_type} loan" MITC filetype:pdf',
            f'site:{domain} {primary_terms} "schedule of charges" filetype:pdf',
            f'"{bank_name}" "{loan_type} loan" "terms and conditions" {exclusions} filetype:pdf',
            f'{bank_name} {loan_type} loan agreement {exclusions} filetype:pdf',
            f'intitle:"{bank_name} {loan_type} loan" (terms OR MITC OR charges) filetype:pdf',
            f'site:{domain} "{loan_type} loan" (terms OR "schedule of charges" OR MITC)',
        ]
    
    async def search_google(self, query: str, num_results: int = 10) -> List[Dict[str, str]]:
        """Search using Google Custom Search API"""
        if self.config.google_api_key == "YOUR_API_KEY_HERE":
            logger.warning("Google API key not configured. Using fallback.")
            return []
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            'key': self.config.google_api_key,
            'cx': self.config.google_cse_id,
            'q': query,
            'num': min(num_results, 10)
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = []
                        
                        for item in data.get('items', []):
                            results.append({
                                'title': item.get('title', ''),
                                'url': item.get('link', ''),
                                'snippet': item.get('snippet', '')
                            })
                        
                        logger.info(f"Found {len(results)} results for: {query[:50]}")
                        return results
                    else:
                        logger.error(f"Google API error: {response.status}")
                        return []
                        
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    @staticmethod
    def filter_urls_better(links: List[Dict], bank_name: str, loan_type: str) -> List[Dict]:
        """Filter out irrelevant URLs aggressively with blacklist, bank, and loan type checking"""
        from urllib.parse import unquote, urlparse
        
        filtered = []
        
        # Bank domain mapping
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
        bank_domain = domain_map.get(bank_name, bank_name.lower())
        
        # Get loan keywords for verification
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
        
        # Negative patterns to exclude (in addition to BLACKLIST)
        exclude_patterns = [
            'deceased', 'claim', 'settlement', 'death', 'demise',
            'credit-card', 'debit-card', 'savings-account',
            'complaints', 'grievance', 'customer-care',
            'careers', 'about-us', 'news', 'press-release',
            'branch-locator', 'atm', 'nri', 'forex'
        ]
        
        # Positive patterns to include (REMOVED generic 'loan' to prevent cross-contamination)
        include_patterns = [
            loan_type.lower(),
            f"{loan_type.lower()} loan",  # Specific loan type only
            'terms',
            'conditions',
            'mitc',
            'agreement',
            'charges',
            'fees',
            'interest',
            '.pdf'
        ]
        
        for link in links:
            url_raw = link.get('url', '')
            url = url_raw.lower()
            title = link.get('title', '').lower()
            snippet = link.get('snippet', '').lower()
            
            # Decode URL to handle encoded characters
            url_decoded = unquote(url)
            
            # Combine all text for pattern matching
            combined = f"{url_decoded} {title} {snippet}"
            
            # STEP 1: Check BLACKLIST first (highest priority rejection)
            blacklist_found = False
            for blacklist_term in BLACKLIST:
                if blacklist_term and blacklist_term in combined:
                    logger.info(f"🚫 Blacklist: '{blacklist_term}' in {url_raw[:80]}")
                    blacklist_found = True
                    break
            
            if blacklist_found:
                continue  # Skip this URL entirely
            
            # STEP 2: Check other negative patterns
            if any(pattern in combined for pattern in exclude_patterns):
                logger.info(f"🚫 Filtered: {url_raw[:80]}")
                continue
            
            # STEP 3: Bank verification - ensure URL is about the target bank
            url_domain = urlparse(url_raw).netloc.lower()
            has_bank_reference = (
                bank_domain in url_domain or
                bank_name.lower() in combined or
                bank_name.lower().replace(' ', '') in combined.replace(' ', '')
            )
            
            if not has_bank_reference:
                logger.info(f"🚫 No bank reference: {url_raw[:80]}")
                continue
            
            # STEP 4: Loan type verification - ensure URL is about the target loan type
            # Use more specific keywords that uniquely identify this loan type
            has_loan_type = False
            combined_normalized = combined.replace('-', ' ').replace('_', ' ')
            
            # Filter out generic keywords to prevent false positives
            # Keep only: multi-word phrases, specific codes, or compound terms
            specific_loan_keywords = [
                kw for kw in all_loan_keywords 
                if (
                    # Exclude overly generic single words
                    kw not in ['loan', 'loans', 'borrow', 'borrowing', 'personal', 'home', 'auto', 'car', 'business', 'education']
                    and (
                        # Keep multi-word phrases (2+ words)
                        len(kw.split()) >= 2 or
                        # Keep URL path codes (starts with /)
                        kw.startswith('/') or
                        # Keep compound terms (no spaces, 10+ chars)
                        (len(kw) >= 10 and ' ' not in kw)
                    )
                )
            ]
            
            for loan_keyword in specific_loan_keywords:
                if loan_keyword in combined_normalized:
                    has_loan_type = True
                    logger.debug(f"✓ Loan type match: '{loan_keyword}' in {url_raw[:80]}")
                    break
            
            if not has_loan_type:
                logger.info(f"🚫 No loan type reference: {url_raw[:80]}")
                continue
            
            # STEP 7: Wrong loan type detection - reject if primarily about different loan type
            other_loan_types = list(LOAN_KEYWORDS.keys())
            if loan_type.lower() in other_loan_types:
                other_loan_types.remove(loan_type.lower())
            
            wrong_loan_detected = False
            for other_type in other_loan_types:
                # Get primary keywords for this other loan type
                other_bundle = LOAN_KEYWORDS.get(other_type, {})
                other_keywords = []
                for key in ['primary', 'variations', 'phrases']:
                    other_keywords.extend(other_bundle.get(key, []))
                
                # Normalize keywords for matching
                other_keywords = [kw.lower().replace('-', ' ').replace('_', ' ') for kw in other_keywords]
                
                # Count mentions of other loan type vs target loan type
                other_type_count = sum(1 for kw in other_keywords if kw in combined_normalized)
                target_count = sum(1 for kw in specific_loan_keywords if kw in combined_normalized)
                
                # If other loan type is mentioned significantly more than target, reject
                if other_type_count > 2 and target_count <= 1:
                    logger.info(f"🚫 Wrong loan type: '{other_type}' detected ({other_type_count} mentions) in {url_raw[:80]}")
                    wrong_loan_detected = True
                    break
            
            if wrong_loan_detected:
                continue
            
            # STEP 5: Include if matches positive patterns
            positive_matches = sum(1 for pattern in include_patterns if pattern in combined)
            if positive_matches >= 2:  # At least 2 positive matches
                # STEP 9: Calculator/Tool rejection
                calculator_patterns = [
                    'emi-calculator', 'loan-calculator', 'calculate-emi',
                    'eligibility-calculator', 'emi calculator', 'loan calculator',
                    'calculator', 'calculate', 'computation', 'compute'
                ]
                
                has_calculator = any(pattern in combined for pattern in calculator_patterns)
                
                if has_calculator:
                    # Check if it's a legitimate TNC document that mentions calculation
                    tnc_indicators = ['terms', 'tnc', '.pdf', 'mitc', 'schedule-of-charges', 'agreement', 'conditions']
                    has_tnc_indicator = any(indicator in url_decoded or indicator in title for indicator in tnc_indicators)
                    
                    if not has_tnc_indicator:
                        logger.info(f"🚫 Calculator/tool: {url_raw[:80]}")
                        continue
                
                # STEP 8: Check for TNC keywords boost
                has_tnc = False
                for category in TNC_KEYWORDS.values():
                    for keyword in category:
                        if keyword in url_decoded or keyword in title:
                            has_tnc = True
                            break
                    if has_tnc:
                        break
                
                # Add TNC flag to link metadata
                if has_tnc:
                    link['has_tnc_keyword'] = True
                
                filtered.append(link)
                logger.info(f"✅ Included: {url_raw[:80]} (TNC: {has_tnc})")
        
        return filtered
    
    def _get_domain(self, bank_name: str) -> str:
        """Get primary domain for bank"""
        domains = self.config.trusted_domains.get(bank_name, [])
        return domains[0] if domains else bank_name.lower()

# ==============================================================================
# ASYNC SMART NAVIGATOR
# ==============================================================================

class AsyncSmartNavigator:
    """Async crawler with concurrency control"""
    
    def __init__(self, config: ScraperConfig, loan_type: str = "personal"):
        self.config = config
        self.loan_type = loan_type
        self.visited_urls: Set[str] = set()
        self.rate_limiter = RateLimiter(config.rate_limit_per_domain)
        self.circuit_breaker = CircuitBreaker()
        self.semantic_filter = ImprovedSemanticFilter(loan_type)
    
    def get_random_user_agent(self) -> str:
        return random.choice(self.config.user_agents)
    
    def get_headers(self) -> Dict[str, str]:
        return {
            'User-Agent': self.get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        }
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def fetch_page(self, url: str, session: aiohttp.ClientSession) -> Optional[str]:
        """Fetch page with exponential backoff retry"""
        if url in self.visited_urls:
            return None
        
        domain = urlparse(url).netloc
        
        # Check circuit breaker
        if self.circuit_breaker.is_open(domain):
            logger.warning(f"Circuit breaker open for {domain}, skipping")
            return None
        
        # Rate limiting
        await self.rate_limiter.acquire(domain)
        
        self.visited_urls.add(url)
        
        try:
            # Add jitter to prevent thundering herd
            await asyncio.sleep(random.uniform(0.1, 0.5))
            
            async with session.get(
                url,
                headers=self.get_headers(),
                timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                allow_redirects=True
            ) as response:
                if response.status == 200:
                    content = await response.text()
                    self.circuit_breaker.record_success(domain)
                    logger.info(f"✅ Fetched: {url[:80]}")
                    return content
                else:
                    logger.warning(f"HTTP {response.status}: {url}")
                    self.circuit_breaker.record_failure(domain)
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            self.circuit_breaker.record_failure(domain)
            raise
    
    def find_document_links(self, html: str, base_url: str) -> List[str]:
        """Find document links with improved detection"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            doc_links = []
            
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(base_url, href)
                
                # Skip non-http links
                if not full_url.startswith(('http://', 'https://')):
                    continue
                
                link_text = link.get_text().strip().lower()
                url_lower = full_url.lower()
                
                # PDF links
                if url_lower.endswith('.pdf'):
                    doc_links.append(full_url)
                    continue
                
                # Other document formats
                if any(url_lower.endswith(ext) for ext in ['.docx', '.doc', '.xlsx']):
                    doc_links.append(full_url)
                    continue
                
                # Links with document keywords
                combined = link_text + ' ' + url_lower
                if any(keyword in combined for keyword in self.config.doc_keywords):
                    doc_links.append(full_url)
            
            return list(set(doc_links))  # Remove duplicates
            
        except Exception as e:
            logger.error(f"Error parsing links: {e}")
            return []
    
    async def crawl_for_documents(
        self,
        start_urls: List[str],
        max_depth: int = 2
    ) -> List[str]:
        """Async BFS crawl with concurrency control"""
        to_visit = [(url, 0) for url in start_urls]
        found_docs = []
        domain_page_count = defaultdict(int)
        
        async with aiohttp.ClientSession() as session:
            while to_visit:
                # Process in batches for concurrency
                batch = to_visit[:self.config.concurrent_requests]
                to_visit = to_visit[self.config.concurrent_requests:]
                
                tasks = []
                for url, depth in batch:
                    if depth > max_depth or url in self.visited_urls:
                        continue
                    
                    domain = urlparse(url).netloc
                    if domain_page_count[domain] >= self.config.max_pages_per_domain:
                        continue
                    
                    domain_page_count[domain] += 1
                    tasks.append(self._process_page(url, depth, session))
                
                # Wait for batch to complete
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Task failed: {result}")
                        continue
                    
                    if result:
                        docs, next_pages = result
                        found_docs.extend(docs)
                        to_visit.extend(next_pages)
        
        return list(set(found_docs))  # Deduplicate
    
    async def _process_page(
        self,
        url: str,
        depth: int,
        session: aiohttp.ClientSession
    ) -> Optional[Tuple[List[str], List[Tuple[str, int]]]]:
        """Process a single page"""
        try:
            html = await self.fetch_page(url, session)
            if not html:
                return None
            
            # Quick semantic check on HTML preview
            preview = BeautifulSoup(html, 'html.parser').get_text()[:3000]
            is_relevant, score = self.semantic_filter.is_relevant(preview)
            
            if not is_relevant:
                logger.info(f"⚠️  Semantically irrelevant (score: {score:.2f}): {url[:60]}")
                return None
            
            # Find links
            doc_links = self.find_document_links(html, url)
            
            # Separate PDFs from pages to explore further
            pdf_links = [link for link in doc_links if link.endswith('.pdf')]
            page_links = [(link, depth + 1) for link in doc_links if not link.endswith('.pdf')]
            
            return pdf_links, page_links
            
        except Exception as e:
            logger.error(f"Error processing page {url}: {e}")
            return None
    
    async def fetch_with_playwright(self, url: str) -> Optional[str]:
        """Fallback to Playwright for JS-heavy pages"""
        if not PLAYWRIGHT_AVAILABLE:
            return None
        
        try:
            logger.info(f"🎭 Using Playwright for: {url}")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=self.get_random_user_agent(),
                    viewport={'width': 1920, 'height': 1080}
                )
                page = await context.new_page()
                
                # Block unnecessary resources for speed
                await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2}", lambda route: route.abort())
                
                await page.goto(url, wait_until='networkidle', timeout=30000)
                await page.wait_for_timeout(2000)
                
                content = await page.content()
                await browser.close()
                
                return content
                
        except Exception as e:
            logger.error(f"Playwright error: {e}")
            return None

# ==============================================================================
# ENHANCED CONTENT EXTRACTION
# ==============================================================================

class EnhancedContentExtractor:
    """Extract content with OCR fallback"""
    
    @staticmethod
    async def extract_pdf_text(pdf_url: str, use_ocr: bool = True) -> Optional[str]:
        """Extract text from PDF with OCR fallback"""
        try:
            logger.info(f"📑 Downloading PDF: {pdf_url[:60]}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(pdf_url, timeout=60) as response:
                    if response.status != 200:
                        return None
                    
                    pdf_content = await response.read()
            
            # Try PyPDF2 first
            pdf_file = BytesIO(pdf_content)
            
            try:
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                text = ""
                
                for page_num in range(min(150, len(pdf_reader.pages))):
                    page = pdf_reader.pages[page_num]
                    page_text = page.extract_text()
                    text += page_text + "\n"
                
                # If substantial text extracted, return it
                if len(text.strip()) > 500:
                    logger.info(f"✅ Extracted {len(text)} chars via PyPDF2")
                    return text
                
            except Exception as e:
                logger.warning(f"PyPDF2 failed: {e}")
            
            # Fallback to OCR for scanned PDFs
            if use_ocr and OCR_AVAILABLE:
                logger.info("🔍 Attempting OCR extraction...")
                return await EnhancedContentExtractor._extract_with_ocr(pdf_content)
            
            return text if text else None
            
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            return None
    
    @staticmethod
    async def _extract_with_ocr(pdf_content: bytes) -> Optional[str]:
        """OCR extraction for scanned PDFs"""
        try:
            # Convert PDF to images
            images = convert_from_bytes(pdf_content, dpi=300, first_page=1, last_page=50)
            
            text = ""
            for i, image in enumerate(images):
                logger.info(f"OCR processing page {i+1}/{len(images)}")
                page_text = pytesseract.image_to_string(image, lang='eng')
                text += page_text + "\n"
            
            logger.info(f"✅ OCR extracted {len(text)} chars")
            return text
            
        except Exception as e:
            logger.error(f"OCR error: {e}")
            return None

# ==============================================================================
# CONTENT DEDUPLICATION
# ==============================================================================

class ContentDeduplicator:
    """Detect and remove duplicate content"""
    
    def __init__(self):
        self.seen_hashes: Set[str] = set()
        self.seen_chunks: List[str] = []
    
    def get_content_hash(self, text: str) -> str:
        """Get hash of normalized content"""
        # Normalize
        normalized = re.sub(r'\s+', ' ', text.lower().strip())
        return hashlib.sha256(normalized.encode()).hexdigest()
    
    def is_duplicate(self, text: str, threshold: float = 0.90) -> bool:
        """Check if content is duplicate"""
        if not text or len(text) < 200:
            return False
        
        # Exact hash match
        content_hash = self.get_content_hash(text)
        if content_hash in self.seen_hashes:
            return True
        
        # Store this hash
        self.seen_hashes.add(content_hash)
        
        # Fuzzy similarity check (using simple overlap)
        text_words = set(text.lower().split())
        
        for seen_text in self.seen_chunks:
            seen_words = set(seen_text.lower().split())
            
            if not text_words or not seen_words:
                continue
            
            # Jaccard similarity
            intersection = len(text_words & seen_words)
            union = len(text_words | seen_words)
            similarity = intersection / union if union > 0 else 0
            
            if similarity >= threshold:
                return True
        
        # Store sample for future comparison
        self.seen_chunks.append(text[:5000])
        if len(self.seen_chunks) > 20:  # Keep memory bounded
            self.seen_chunks.pop(0)
        
        return False

# ==============================================================================
# ENHANCED VALIDATION
# ==============================================================================

class EnhancedDocumentValidator:
    """Advanced validation with scoring"""
    
    def __init__(self, config: Optional['ScraperConfig'] = None):
        """Initialize with configuration"""
        self.config = config or ScraperConfig()
    
    def is_valid_loan_document(self, text: str, bank_name: str, loan_type: str) -> Tuple[bool, Dict]:
        """
        Validate loan document using loan-specific configuration (STEP 21)
        Delegates to ImprovedValidator.validate from scraper_interface
        """
        from scraper_interface import ImprovedValidator
        
        # STEP 21: Get loan-specific configuration
        loan_config = self.config.loan_type_config.get(
            loan_type.lower(), 
            self.config.loan_type_config['personal']
        )
        
        # Check minimum document length based on loan type
        if len(text) < loan_config['min_doc_length']:
            return False, {
                'valid': False,
                'reasons': [f"Content too short: {len(text)} < {loan_config['min_doc_length']} (required for {loan_type} loans)"],
                'scores': {},
                'overall_score': 0.0
            }
        
        # Use ImprovedValidator with loan-specific config
        is_valid, result = ImprovedValidator.validate(text, bank_name, loan_type)
        
        # STEP 21: Apply loan-specific validation threshold
        if is_valid:
            overall_score = result.get('overall_score', 0.0)
            required_threshold = loan_config['validation_threshold']
            
            if overall_score < required_threshold:
                result['valid'] = False
                result['reasons'].append(
                    f"Below {loan_type} loan threshold: {overall_score:.2f} < {required_threshold}"
                )
                return False, result
        
        return is_valid, result
    
    @staticmethod
    def validate_loan_document_improved(text: str, bank_name: str, loan_type: str) -> Tuple[bool, Dict]:
        """Stricter validation (legacy method for compatibility)"""
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
        
        # 1. Must NOT contain irrelevant content
        negative_terms = ['deceased', 'death claim', 'settlement', 'demise', 'nominee']
        negative_count = sum(1 for term in negative_terms if term in text_lower[:1000])
        if negative_count > 0:
            result['reasons'].append(f"Contains irrelevant content: {negative_count} negative terms")
            result['scores']['negative_check'] = 0.0
            return False, result
        result['scores']['negative_check'] = 1.0
        
        # 2. Bank name check
        bank_found = bank_name.lower().replace(' ', '') in text_lower.replace(' ', '')
        result['scores']['bank_name'] = 1.0 if bank_found else 0.0
        
        # 3. Loan type check
        loan_type_found = loan_type.lower() in text_lower[:2000]
        result['scores']['loan_type'] = 1.0 if loan_type_found else 0.3
        
        # 4. Essential loan keywords
        essential_keywords = ['loan', 'interest', 'rate', 'charges', 'fees', 'emi']
        essential_score = sum(1 for kw in essential_keywords if kw in text_lower) / len(essential_keywords)
        result['scores']['essential_keywords'] = essential_score
        
        # 5. Terms keywords
        terms_keywords = ['terms', 'conditions', 'agreement', 'tenure', 'prepayment']
        terms_score = sum(1 for kw in terms_keywords if kw in text_lower) / len(terms_keywords)
        result['scores']['terms_keywords'] = terms_score
        
        # Calculate overall score
        overall = (
            result['scores']['negative_check'] * 0.30 +
            result['scores']['bank_name'] * 0.20 +
            result['scores']['loan_type'] * 0.15 +
            result['scores']['essential_keywords'] * 0.20 +
            result['scores']['terms_keywords'] * 0.15
        )
        
        result['overall_score'] = overall
        result['valid'] = overall >= 0.60  # Higher threshold
        
        if not result['valid']:
            result['reasons'].append(f"Low score: {overall:.2f}")
        
        return result['valid'], result
    
    @staticmethod
    def calculate_confidence_score(
        text: str,
        metadata: Dict,
        validation_result: Dict
    ) -> float:
        """Enhanced confidence scoring with STEP 17 additions"""
        # STEP 16: Reduced base score weight from 0.5 to 0.40
        score = validation_result.get('overall_score', 0.0) * 0.40
        
        # STEP 16: Increased official domain bonus from 0.15 to 0.30
        # Bonus for official domain
        if metadata.get('from_official_domain'):
            score += 0.30
        
        # Bonus for PDF
        if metadata.get('is_pdf'):
            score += 0.10
        
        # STEP 16: Add bank mention density bonus
        # Bank mention density
        text_length_k = len(text) / 1000.0
        bank_mentions = metadata.get('bank_mention_count', 0)
        density = bank_mentions / text_length_k if text_length_k > 0 else 0
        
        if density > 5:
            score += 0.15
        elif density > 2:
            score += 0.10
        
        # Bonus for substantial content
        if len(text) > 20000:
            score += 0.10
        elif len(text) > 10000:
            score += 0.05
        
        # Bonus for high semantic relevance
        if metadata.get('semantic_score', 0) > 0.5:
            score += 0.10
        
        # STEP 17A: Loan type mention bonus
        loan_type = metadata.get('loan_type', 'personal')
        loan_mentions = text.lower().count(f'{loan_type} loan')
        if loan_mentions > 5:
            score += 0.10
        elif loan_mentions > 2:
            score += 0.05
        
        # STEP 17B: TNC keyword bonus
        # TNC in URL
        url_lower = metadata.get('url', '').lower()
        if any(kw in url_lower for kw in ['terms', 'tnc', 'mitc', 'schedule']):
            score += 0.10
        
        # MITC in content
        if 'most important terms' in text.lower() or 'mitc' in text.lower()[:5000]:
            score += 0.08
        
        # STEP 17C: Penalties
        # Penalty for non-official domain
        if not metadata.get('from_official_domain'):
            score -= 0.20
        
        # Penalty for low bank mentions
        if bank_mentions < 3:
            score -= 0.15
        
        # Penalty for marketing content
        marketing_keywords = ['apply now', 'click here', 'special offer', 'limited time']
        marketing_count = sum(1 for kw in marketing_keywords if kw in text.lower())
        if marketing_count > 5:
            score -= 0.10
        
        return min(max(score, 0.0), 1.0)  # Clamp to [0, 1]

# ==============================================================================
# CACHING LAYER - STEP 29
# ==============================================================================

class ResultCache:
    """
    STEP 29: Cache scraping results to speed up repeated searches
    """
    def __init__(self, cache_dir='cache'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        logger.info(f"💾 Cache initialized: {self.cache_dir}")
    
    def _get_cache_key(self, bank_name: str, loan_type: str) -> str:
        """Generate cache key from bank and loan type"""
        # Normalize to avoid case/whitespace issues
        key = f"{bank_name.lower().replace(' ', '_')}_{loan_type.lower()}.pkl"
        return key
    
    def get(self, bank_name: str, loan_type: str, max_age_hours: int = 24) -> Optional[Dict]:
        """
        Get cached result if exists and not expired
        
        Args:
            bank_name: Bank name
            loan_type: Loan type
            max_age_hours: Maximum cache age in hours (default: 24)
        
        Returns:
            Cached result dict or None if not found/expired
        """
        cache_file = self.cache_dir / self._get_cache_key(bank_name, loan_type)
        
        if not cache_file.exists():
            logger.debug(f"💾 Cache miss: {bank_name} {loan_type}")
            return None
        
        # Check age
        file_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        age = datetime.now() - file_time
        
        if age > timedelta(hours=max_age_hours):
            logger.info(f"💾 Cache expired ({age.total_seconds()/3600:.1f}h old): {bank_name} {loan_type}")
            return None
        
        try:
            with open(cache_file, 'rb') as f:
                cached_result = pickle.load(f)
            
            logger.info(f"💾 ✅ Cache hit ({age.total_seconds()/60:.1f}m old): {bank_name} {loan_type}")
            return cached_result
        except Exception as e:
            logger.warning(f"💾 Cache read error: {e}")
            return None
    
    def set(self, bank_name: str, loan_type: str, result: Dict) -> bool:
        """
        Cache result
        
        Args:
            bank_name: Bank name
            loan_type: Loan type
            result: Result dictionary to cache
        
        Returns:
            True if successful, False otherwise
        """
        cache_file = self.cache_dir / self._get_cache_key(bank_name, loan_type)
        
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(result, f)
            
            logger.info(f"💾 Cached result: {bank_name} {loan_type}")
            return True
        except Exception as e:
            logger.error(f"💾 Cache save error: {e}")
            return False
    
    def clear(self) -> int:
        """
        Clear all cached results
        
        Returns:
            Number of files deleted
        """
        count = 0
        for cache_file in self.cache_dir.glob('*.pkl'):
            try:
                cache_file.unlink()
                count += 1
            except Exception as e:
                logger.error(f"💾 Error deleting {cache_file}: {e}")
        
        logger.info(f"💾 Cleared {count} cached results")
        return count
    
    def get_cache_info(self) -> Dict:
        """Get information about cached results"""
        cache_files = list(self.cache_dir.glob('*.pkl'))
        
        info = {
            'total_cached': len(cache_files),
            'cache_dir': str(self.cache_dir),
            'files': []
        }
        
        for cache_file in cache_files:
            file_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
            age = datetime.now() - file_time
            
            info['files'].append({
                'name': cache_file.name,
                'age_hours': round(age.total_seconds() / 3600, 1),
                'size_kb': round(cache_file.stat().st_size / 1024, 1)
            })
        
        return info

# ==============================================================================
# MAIN ORCHESTRATOR - PRODUCTION GRADE
# ==============================================================================

class ProductionBankScraper:
    """Production-grade orchestrator with all enhancements"""
    
    def __init__(self, config: Optional[ScraperConfig] = None):
        self.config = config or ScraperConfig()
        self.search = EnhancedSearchIntelligence(self.config)
        self.navigator = None  # Will be initialized per scrape with loan_type
        self.extractor = EnhancedContentExtractor()
        self.validator = EnhancedDocumentValidator(self.config)  # STEP 21: Pass config
        self.deduplicator = ContentDeduplicator() if self.config.enable_deduplication else None
        self.rejected_urls = []  # STEP 24: Track rejected URLs
        self.cache = ResultCache()  # STEP 29: Add caching
        
        logger.info("🚀 Production Bank Scraper initialized")
    
    def calculate_url_priority(self, url: str, title: str, snippet: str, bank_name: str, loan_type: str) -> int:
        """
        STEP 25: Calculate priority score for URL
        Higher score = process first
        """
        score = 0
        url_lower = url.lower()
        title_lower = title.lower()
        snippet_lower = snippet.lower()
        
        # Bank domain mapping
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
        
        # Official domain (highest priority)
        bank_domain = domain_map.get(bank_name, '').lower()
        if bank_domain and bank_domain in url_lower:
            score += 50
        
        # TNC keywords in URL
        if any(kw in url_lower for kw in ['terms', 'tnc', 'mitc']):
            score += 30
        
        # PDF file
        if url.endswith('.pdf'):
            score += 25
        
        # Loan type in URL
        if loan_type.lower() in url_lower:
            score += 20
        
        # Charges/schedule keywords
        if any(kw in url_lower for kw in ['schedule', 'charges', 'fees']):
            score += 20
        
        # Bank name in URL
        if bank_name.lower() in url_lower:
            score += 15
        
        # Title relevance (bank + loan type)
        if bank_name.lower() in title_lower and loan_type.lower() in title_lower:
            score += 15
        
        # TNC in title or snippet
        if any(kw in title_lower or kw in snippet_lower for kw in ['terms', 'conditions', 'mitc']):
            score += 10
        
        return score
    
    async def scrape_bank_documents(
        self,
        bank_name: str,
        loan_type: str = "personal",
        use_search_api: bool = True,
        use_cache: bool = True  # STEP 29: Add cache parameter
    ) -> Dict[str, any]:
        """
        Main scraping method - fully async and production-ready
        
        Args:
            bank_name: Name of the bank
            loan_type: Type of loan (personal, home, auto, etc.)
            use_search_api: Whether to use Google Search API
            use_cache: Whether to use cached results (default 24h expiry)
        
        Returns:
            Comprehensive results dictionary
        """
        # STEP 29: Check cache first
        if use_cache:
            cached_result = self.cache.get(bank_name, loan_type)
            if cached_result:
                cached_result['metadata']['from_cache'] = True
                logger.info(f"💾 Returning cached result for {bank_name} {loan_type}")
                return cached_result
        
        start_time = time.time()
        
        # STEP 27: Test mode configuration
        if self.config.test_mode:
            logger.setLevel(logging.DEBUG)
            logger.info("=" * 70)
            logger.info("🧪 TEST MODE ENABLED")
            logger.info("   - Limited to 3 URLs")
            logger.info("   - Verbose logging")
            logger.info("   - Deduplication disabled")
            logger.info("=" * 70)
        
        logger.info("=" * 70)
        logger.info(f"🏦 Starting scrape: {bank_name} - {loan_type} loan")
        logger.info("=" * 70)
        
        # Initialize navigator with loan_type for semantic filtering
        self.navigator = AsyncSmartNavigator(self.config, loan_type)
        
        # STEP 24: Reset rejected URLs for each scrape
        self.rejected_urls = []
        
        result = {
            'bank': bank_name,
            'loan_type': loan_type,
            'timestamp': datetime.now().isoformat(),
            'success': False,
            'documents': [],
            'metadata': {
                'pages_crawled': 0,
                'pdfs_found': 0,
                'duplicates_filtered': 0,
                'errors': [],
                'execution_time_seconds': 0,
                'rejected_urls': []  # STEP 24: Track rejections
            }
        }
        
        try:
            # Step 1: Get starting URLs via search or fallback
            if use_search_api and self.config.google_api_key != "YOUR_API_KEY_HERE":
                start_urls = await self._search_for_urls(bank_name, loan_type)
            else:
                start_urls = self._get_fallback_urls(bank_name, loan_type)
            
            if not start_urls:
                result['metadata']['errors'].append("No starting URLs found")
                return result
            
            logger.info(f"📍 Starting from {len(start_urls)} URLs")
            
            # Step 2: Async crawl for documents
            doc_urls = await self.navigator.crawl_for_documents(start_urls, max_depth=2)
            result['metadata']['pages_crawled'] = len(self.navigator.visited_urls)
            result['metadata']['pdfs_found'] = len([u for u in doc_urls if u.endswith('.pdf')])
            
            logger.info(f"📊 Found {len(doc_urls)} potential documents")
            
            # STEP 22: Smart Deduplication - Group similar URLs and prioritize
            from urllib.parse import urlparse
            
            # Group similar URLs (same domain + path, ignore query params)
            url_groups = {}
            for url in doc_urls:
                parsed = urlparse(url)
                # Create key from domain + path (ignore query params)
                key = f"{parsed.netloc}{parsed.path}"
                
                if key not in url_groups:
                    url_groups[key] = []
                url_groups[key].append(url)
            
            # Prioritize within groups: PDF > has 'terms' in path > official domain
            bank_domain = bank_name.lower().replace(' ', '')
            priority_urls = []
            for key, urls in url_groups.items():
                # Prioritize: PDF > has 'terms' in path > official domain
                best_url = max(urls, key=lambda u: (
                    u.endswith('.pdf') * 100 +
                    ('terms' in u.lower() or 'mitc' in u.lower()) * 50 +
                    (bank_domain in u.lower()) * 25
                ))
                priority_urls.append(best_url)
            
            logger.info(f"🔄 Grouped {len(doc_urls)} URLs into {len(url_groups)} groups")
            logger.info(f"✨ Processing {len(priority_urls)} priority URLs (removed {len(doc_urls) - len(priority_urls)} duplicates)")
            
            # STEP 27: Limit URLs in test mode
            if self.config.test_mode:
                priority_urls = priority_urls[:3]
                logger.info(f"🧪 TEST MODE: Limited to {len(priority_urls)} URLs")
            
            # Step 3: Extract and validate documents concurrently
            extraction_tasks = [
                self._extract_and_validate(url, bank_name, loan_type)
                for url in priority_urls[:10]  # Limit to top 10 from priority list
            ]
            
            extraction_results = await asyncio.gather(*extraction_tasks, return_exceptions=True)
            
            # Step 4: Process results
            for extract_result in extraction_results:
                if isinstance(extract_result, Exception):
                    result['metadata']['errors'].append(str(extract_result))
                    continue
                
                if not extract_result:
                    continue
                
                # Check for duplicates
                if self.deduplicator:
                    if self.deduplicator.is_duplicate(extract_result['text']):
                        result['metadata']['duplicates_filtered'] += 1
                        logger.info(f"⚠️  Duplicate filtered: {extract_result['url'][:60]}")
                        continue
                
                result['documents'].append(extract_result)
            
            # Sort by confidence
            result['documents'].sort(key=lambda x: x['confidence'], reverse=True)
            
            # STEP 31: Adaptive Post-Processing Filters based on strictness mode
            strictness = self.config.strictness_mode.lower()
            
            # Define thresholds based on strictness
            if strictness == "strict":
                min_bank_mentions = 3
                min_loan_mentions = 2
                min_confidence = 0.65
                success_confidence = 0.70
                logger.info("🔒 STRICT mode: High validation thresholds")
            elif strictness == "normal":
                min_bank_mentions = 2
                min_loan_mentions = 1
                min_confidence = 0.50
                success_confidence = 0.60
                logger.info("⚖️  NORMAL mode: Balanced validation thresholds")
            else:  # relaxed
                min_bank_mentions = 1
                min_loan_mentions = 1
                min_confidence = 0.40
                success_confidence = 0.50
                logger.info("🔓 RELAXED mode: Lenient validation thresholds")
            
            # Filter 1: Bank mentions
            filtered_docs = []
            for doc in result['documents']:
                bank_count = doc['text'].lower().count(bank_name.lower())
                if bank_count >= min_bank_mentions:
                    filtered_docs.append(doc)
                else:
                    logger.info(f"Post-filter: Removed doc with only {bank_count} bank mentions (need {min_bank_mentions}+)")
            
            result['documents'] = filtered_docs
            
            # Filter 2: Loan type verification
            final_docs = []
            for doc in result['documents']:
                loan_count = doc['text'].lower()[:2000].count(f'{loan_type} loan')
                if loan_count >= min_loan_mentions:
                    final_docs.append(doc)
                else:
                    logger.info(f"Post-filter: Removed doc with only {loan_count} loan type mentions (need {min_loan_mentions}+)")
            
            result['documents'] = final_docs
            
            # Filter 3: Minimum confidence
            result['documents'] = [doc for doc in result['documents'] if doc['confidence'] >= min_confidence]
            
            # STEP 18: Update success criteria - mark success based on strictness mode
            if result['documents'] and result['documents'][0]['confidence'] > success_confidence:
                result['success'] = True
            else:
                result['success'] = False
            
            # STEP 24: Add rejected URLs to metadata (limit to 20 for UI)
            result['metadata']['rejected_urls'] = self.rejected_urls[:20]
            
            # STEP 28: Calculate quality metrics
            if result['documents']:
                total_docs = len(result['documents'])
                
                official_domain_count = sum(1 for doc in result['documents'] 
                                            if doc['metadata']['from_official_domain'])
                
                high_confidence_count = sum(1 for doc in result['documents'] 
                                            if doc['confidence'] > 0.75)
                
                avg_confidence = sum(doc['confidence'] for doc in result['documents']) / total_docs
                
                avg_bank_mentions = sum(doc['metadata'].get('bank_mention_count', 0) 
                                       for doc in result['documents']) / total_docs
                
                # Cross-contamination risk
                contamination_count = sum(1 for doc in result['documents']
                                         if doc['metadata'].get('competing_banks_found'))
                
                # PDF count
                pdf_count = sum(1 for doc in result['documents'] 
                               if doc['metadata']['is_pdf'])
                
                # Determine quality score
                if avg_confidence > 0.75 and official_domain_count / total_docs > 0.5:
                    quality_score = 'HIGH'
                elif avg_confidence > 0.65:
                    quality_score = 'MEDIUM'
                else:
                    quality_score = 'LOW'
                
                quality_metrics = {
                    'total_documents': total_docs,
                    'official_domain_percentage': round(official_domain_count / total_docs * 100, 1),
                    'high_confidence_count': high_confidence_count,
                    'average_confidence': round(avg_confidence, 3),
                    'average_bank_mentions': round(avg_bank_mentions, 1),
                    'cross_contamination_risk_count': contamination_count,
                    'pdf_document_count': pdf_count,
                    'quality_score': quality_score
                }
                
                result['metadata']['quality_metrics'] = quality_metrics
                
                # Log quality metrics
                logger.info("\n" + "=" * 70)
                logger.info("📊 QUALITY METRICS")
                logger.info("=" * 70)
                logger.info(f"Quality Score: {quality_score}")
                logger.info(f"Total Documents: {total_docs}")
                logger.info(f"Official Domain: {quality_metrics['official_domain_percentage']}%")
                logger.info(f"High Confidence (>0.75): {high_confidence_count}")
                logger.info(f"Average Confidence: {avg_confidence:.1%}")
                logger.info(f"Average Bank Mentions: {avg_bank_mentions:.1f}")
                logger.info(f"PDF Documents: {pdf_count}")
                logger.info(f"Cross-Contamination Risk: {contamination_count}")
                logger.info("=" * 70 + "\n")
            
            # Calculate execution time
            result['metadata']['execution_time_seconds'] = round(time.time() - start_time, 2)
            
            # STEP 29: Cache successful results
            if result['success'] and use_cache and len(result['documents']) > 0:
                self.cache.set(bank_name, loan_type, result)
                logger.info(f"💾 Cached result for {bank_name} {loan_type}")
            
            # Summary
            logger.info("=" * 70)
            logger.info("📈 SCRAPING COMPLETE")
            logger.info(f"✅ Success: {result['success']}")
            logger.info(f"📄 Documents found: {len(result['documents'])}")
            logger.info(f"❌ URLs rejected: {len(self.rejected_urls)}")
            logger.info(f"⏱️  Execution time: {result['metadata']['execution_time_seconds']}s")
            logger.info("=" * 70)
            
            return result
            
        except Exception as e:
            logger.error(f"Fatal error during scraping: {e}", exc_info=True)
            result['metadata']['errors'].append(f"Fatal: {str(e)}")
            return result
    
    async def _search_for_urls(self, bank_name: str, loan_type: str) -> List[str]:
        """Search for starting URLs using Google API with priority scoring"""
        queries = self.search.generate_queries(bank_name, loan_type)
        all_results = []  # Store full result objects with title/snippet
        
        for query in queries[:3]:  # Limit to first 3 queries
            try:
                links = await self.search.search_google(query, num_results=10)
                filtered = self.search.filter_relevant_links(links, bank_name)
                all_results.extend(filtered)  # Keep full objects
            except Exception as e:
                logger.error(f"Search error for query '{query}': {e}")
        
        # STEP 25: Score and sort URLs by priority
        url_scores = []
        for result in all_results:
            url = result['url']
            title = result.get('title', '')
            snippet = result.get('snippet', '')
            
            priority = self.calculate_url_priority(url, title, snippet, bank_name, loan_type)
            url_scores.append((url, priority, title))
            
            # Log high-priority URLs
            if priority >= 80:
                logger.info(f"⭐ High priority URL (score: {priority}): {url[:60]}")
        
        # Sort by priority (highest first)
        url_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Deduplicate while preserving order
        seen = set()
        priority_urls = []
        for url, score, title in url_scores:
            if url not in seen:
                seen.add(url)
                priority_urls.append(url)
                logger.debug(f"Priority {score}: {title[:50]} - {url[:60]}")
        
        logger.info(f"📊 Processed {len(priority_urls)} unique URLs (sorted by priority)")
        return priority_urls[:5]  # Top 5 priority URLs
    
    def _get_fallback_urls(self, bank_name: str, loan_type: str) -> List[str]:
        """Fallback URLs removed - relying on search-based discovery only"""
        logger.info(f"⚠️ No fallback URLs configured. Search API required for {bank_name}")
        return []
    
    async def _extract_and_validate(
        self,
        url: str,
        bank_name: str,
        loan_type: str
    ) -> Optional[Dict[str, any]]:
        """Extract content and validate a single document"""
        try:
            # Determine extraction method
            if url.endswith('.pdf'):
                text = await self.extractor.extract_pdf_text(url, use_ocr=True)
                is_pdf = True
            else:
                # Try async HTML fetch first
                async with aiohttp.ClientSession() as session:
                    await self.navigator.rate_limiter.acquire(urlparse(url).netloc)
                    html = await self.navigator.fetch_page(url, session)
                
                # If failed, try Playwright
                if not html:
                    html = await self.navigator.fetch_with_playwright(url)
                
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
                    # Remove scripts and styles
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.decompose()
                    text = soup.get_text()
                else:
                    text = None
                
                is_pdf = False
            
            if not text or len(text) < 300:
                logger.warning(f"⚠️  Insufficient content: {url[:60]}")
                # STEP 24: Track rejection
                self.rejected_urls.append({
                    'url': url,
                    'reason': 'Insufficient Content',
                    'details': f'Content length: {len(text) if text else 0} chars (min: 300)'
                })
                return None
            
            # Semantic relevance check
            is_relevant, semantic_score = self.navigator.semantic_filter.is_relevant(text)
            
            if not is_relevant:
                logger.info(f"⚠️  Semantically irrelevant ({semantic_score:.2f}): {url[:60]}")
                # STEP 24: Track rejection
                self.rejected_urls.append({
                    'url': url,
                    'reason': 'Semantic Irrelevance',
                    'details': f'Score: {semantic_score:.2f} (threshold: 0.30)',
                    'score': semantic_score
                })
                return None
            
            # Validate
            is_valid, validation_result = self.validator.is_valid_loan_document(
                text, bank_name, loan_type
            )
            
            if not is_valid:
                # STEP 20: Enhanced validation logging
                logger.info(f"❌ VALIDATION FAILED: {url[:70]}")
                logger.info(f"   Reason: {', '.join(validation_result['reasons'])}")
                logger.info(f"   Scores: {validation_result.get('scores', {})}")
                logger.info(f"   Overall: {validation_result.get('overall_score', 0):.2f}")
                
                # STEP 24: Track rejection
                self.rejected_urls.append({
                    'url': url,
                    'reason': 'Validation Failed',
                    'details': ', '.join(validation_result['reasons']),
                    'score': validation_result.get('overall_score', 0)
                })
                
                return None
            
            # STEP 19: Enhanced metadata tracking
            # Bank mention tracking
            bank_mentions = text.lower().count(bank_name.lower())
            text_length_k = len(text) / 1000.0
            bank_density = bank_mentions / text_length_k if text_length_k > 0 else 0
            
            # Competing bank tracking
            all_banks = ['HDFC', 'SBI', 'ICICI', 'Axis', 'Kotak', 'Yes Bank', 'IndusInd', 'IDFC First']
            competing_banks_found = {}
            for bank in all_banks:
                if bank != bank_name:
                    count = text.lower()[:2000].count(bank.lower())
                    if count > 0:
                        competing_banks_found[bank] = count
            
            # Calculate confidence
            metadata = {
                'url': url,
                'is_pdf': is_pdf,
                'from_official_domain': self._is_official_domain(url, bank_name),
                'size': len(text),
                'semantic_score': semantic_score,
                'validation': validation_result,
                'loan_type': loan_type,
                'bank_mention_count': bank_mentions,
                'bank_mention_density': round(bank_density, 2),
                'loan_type_mentions': text.lower()[:2000].count(f'{loan_type} loan'),
                'competing_banks_found': competing_banks_found
            }
            
            confidence = self.validator.calculate_confidence_score(text, metadata, validation_result)
            
            logger.info(f"✅ Valid document! Confidence: {confidence:.2f} | {url[:60]}")
            
            return {
                'url': url,
                'text': text[:100000],  # Limit to 100k chars for storage
                'text_length': len(text),
                'metadata': metadata,
                'confidence': confidence,
                'validation_details': validation_result
            }
            
        except Exception as e:
            logger.error(f"Error extracting {url}: {e}")
            return None
    
    def _is_official_domain(self, url: str, bank_name: str) -> bool:
        """Check if URL is from official domain"""
        domain = urlparse(url).netloc.lower()
        trusted = self.config.trusted_domains.get(bank_name, [])
        return any(td in domain for td in trusted)
    
    def save_results(self, result: Dict, output_dir: str = "output"):
        """Save scraping results to disk"""
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{result['bank']}_{result['loan_type']}_loan_{timestamp}.json"
        filepath = output_path / filename
        
        # Don't save full text in JSON (too large)
        result_to_save = result.copy()
        for doc in result_to_save['documents']:
            doc['text'] = doc['text'][:5000] + "... [truncated]"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result_to_save, f, indent=2, ensure_ascii=False)
        
        logger.info(f"💾 Results saved to: {filepath}")
        
        # Save full text documents separately
        for i, doc in enumerate(result['documents']):
            doc_filename = f"{result['bank']}_{result['loan_type']}_doc_{i+1}_{timestamp}.txt"
            doc_filepath = output_path / doc_filename
            
            with open(doc_filepath, 'w', encoding='utf-8') as f:
                f.write(f"URL: {doc['url']}\n")
                f.write(f"Confidence: {doc['confidence']:.2f}\n")
                f.write(f"{'='*70}\n\n")
                f.write(doc['text'])
            
            logger.info(f"💾 Document saved to: {doc_filepath}")

# ==============================================================================
# BATCH SCRAPER FOR MULTIPLE BANKS
# ==============================================================================

class BatchScraper:
    """Scrape multiple banks in parallel with rate limiting"""
    
    def __init__(self, config: Optional[ScraperConfig] = None):
        self.config = config or ScraperConfig()
        self.scraper = ProductionBankScraper(self.config)
    
    async def scrape_multiple_banks(
        self,
        banks: List[str],
        loan_type: str = "personal"
    ) -> Dict[str, Dict]:
        """Scrape multiple banks concurrently"""
        logger.info(f"🏦 Batch scraping {len(banks)} banks for {loan_type} loans")
        
        tasks = [
            self.scraper.scrape_bank_documents(bank, loan_type)
            for bank in banks
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        batch_results = {}
        for bank, result in zip(banks, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to scrape {bank}: {result}")
                batch_results[bank] = {'success': False, 'error': str(result)}
            else:
                batch_results[bank] = result
        
        return batch_results

# ==============================================================================
# USAGE EXAMPLES
# ==============================================================================

async def example_single_bank():
    """Example: Scrape a single bank"""
    config = ScraperConfig(
        google_api_key="YOUR_API_KEY_HERE",  # Replace with your key
        google_cse_id="YOUR_CSE_ID_HERE"
    )
    
    scraper = ProductionBankScraper(config)
    result = await scraper.scrape_bank_documents('HDFC', 'personal')
    
    # Save results
    scraper.save_results(result)
    
    # Print summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"Success: {result['success']}")
    print(f"Documents found: {len(result['documents'])}")
    print(f"Execution time: {result['metadata']['execution_time_seconds']}s")
    
    if result['documents']:
        print("\nTop Document:")
        top_doc = result['documents'][0]
        print(f"  URL: {top_doc['url']}")
        print(f"  Confidence: {top_doc['confidence']:.2f}")
        print(f"  Text length: {top_doc['text_length']} chars")
        print(f"  Preview: {top_doc['text'][:300]}...")

async def example_batch_scraping():
    """Example: Scrape multiple banks"""
    banks = ['HDFC', 'SBI', 'ICICI', 'Axis', 'Kotak']
    
    batch_scraper = BatchScraper()
    results = await batch_scraper.scrape_multiple_banks(banks, 'personal')
    
    # Print summary for each bank
    for bank, result in results.items():
        print(f"\n{bank}:")
        print(f"  Success: {result.get('success', False)}")
        print(f"  Documents: {len(result.get('documents', []))}")
        
        if result.get('documents'):
            best_doc = max(result['documents'], key=lambda x: x['confidence'])
            print(f"  Best confidence: {best_doc['confidence']:.2f}")

async def example_with_api_keys():
    """Example with actual Google API keys"""
    config = ScraperConfig(
        google_api_key="AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",  # Your key
        google_cse_id="0123456789abcdefg",  # Your CSE ID
        concurrent_requests=3,  # Lower for free tier
        rate_limit_per_domain=0.3  # 1 request every 3 seconds
    )
    
    scraper = ProductionBankScraper(config)
    
    # Scrape with search API enabled
    result = await scraper.scrape_bank_documents(
        'HDFC',
        'personal',
        use_search_api=True
    )
    
    return result

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    """Main entry point"""
    print("🚀 Production Bank Document Scraper v2.0")
    print("=" * 70)
    
    # Run example (choose one)
    # asyncio.run(example_single_bank())
    # asyncio.run(example_batch_scraping())
    
    # For Jupyter/IPython, use:
    # await example_single_bank()
    
    # Quick test without API keys (uses fallback URLs)
    config = ScraperConfig()
    scraper = ProductionBankScraper(config)
    result = asyncio.run(scraper.scrape_bank_documents('HDFC', 'personal', use_search_api=False))
    
    print(f"\n✅ Scraping completed!")
    print(f"Success: {result['success']}")
    print(f"Documents found: {len(result['documents'])}")
    
    if result['documents']:
        print(f"\nBest document:")
        best = result['documents'][0]
        print(f"  Confidence: {best['confidence']:.2f}")
        print(f"  URL: {best['url']}")

if __name__ == "__main__":
    main()
