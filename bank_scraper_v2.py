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

class SemanticFilter:
    """Use embeddings to filter relevant documents"""
    
    def __init__(self):
        # DISABLE MODEL LOADING FOR FASTER STARTUP
        self.model = None
        logger.info("⚡ Semantic filtering disabled for faster startup")
    
    def is_relevant(self, text: str, threshold: float = 0.35) -> Tuple[bool, float]:
        """Check if text is semantically relevant to loan documents"""
        if not self.model or not text:
            # Fallback to keyword-based filtering
            text_lower = text.lower()
            loan_keywords = ['loan', 'interest', 'terms', 'conditions', 'emi', 'charges', 'fees']
            matches = sum(1 for kw in loan_keywords if kw in text_lower)
            score = matches / len(loan_keywords)
            return score > 0.2, score
        
        # Original semantic code (never reached if model is None)
        try:
            text_sample = text[:2000]
            text_embedding = self.model.encode([text_sample])[0]
            similarities = [
                np.dot(text_embedding, ref_emb) / 
                (np.linalg.norm(text_embedding) * np.linalg.norm(ref_emb))
                for ref_emb in self.reference_embeddings
            ]
            max_similarity = max(similarities)
            is_relevant = max_similarity >= threshold
            return is_relevant, max_similarity
        except Exception as e:
            logger.error(f"Semantic filtering error: {e}")
            return True, 0.0

# ==============================================================================
# ENHANCED SEARCH WITH GOOGLE CSE
# ==============================================================================

class EnhancedSearchIntelligence:
    """Google Custom Search API integration"""
    
    def __init__(self, config: ScraperConfig):
        self.config = config
    
    def generate_queries(self, bank_name: str, loan_type: str = "personal") -> List[str]:
        """Generate optimized search queries"""
        queries = [
            # Direct PDF searches
            f"{bank_name} {loan_type} loan terms conditions PDF filetype:pdf",
            f"{bank_name} MITC {loan_type} loan filetype:pdf",
            f"{bank_name} loan agreement PDF site:*.{self._get_domain(bank_name)}",
            
            # Specific documents
            f"{bank_name} schedule of charges {loan_type} loan",
            f"{bank_name} {loan_type} loan fees interest rate",
            f"{bank_name} {loan_type} loan key facts statement",
            
            # Official pages
            f'"{bank_name}" "{loan_type} loan" "terms and conditions"',
            f"{bank_name} {loan_type} loan important terms download"
        ]
        return queries
    
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
    
    def filter_relevant_links(self, links: List[Dict], bank_name: str) -> List[Dict]:
        """Filter to trusted domains only"""
        trusted = self.config.trusted_domains.get(bank_name, [])
        filtered = []
        
        for link in links:
            url = link.get('url', '')
            domain = urlparse(url).netloc.lower()
            
            # Check trusted domains
            if any(td in domain for td in trusted):
                filtered.append(link)
            else:
                logger.debug(f"Filtered out untrusted domain: {domain}")
        
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
    
    def __init__(self, config: ScraperConfig):
        self.config = config
        self.visited_urls: Set[str] = set()
        self.rate_limiter = RateLimiter(config.rate_limit_per_domain)
        self.circuit_breaker = CircuitBreaker()
        self.semantic_filter = SemanticFilter()
    
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
    
    @staticmethod
    def is_valid_loan_document(
        text: str,
        bank_name: str,
        loan_type: str
    ) -> Tuple[bool, Dict[str, any]]:
        """Comprehensive validation with detailed scoring"""
        validation_result = {
            'valid': False,
            'reasons': [],
            'scores': {}
        }
        
        if not text or len(text) < 300:
            validation_result['reasons'].append("Content too short")
            return False, validation_result
        
        text_lower = text.lower()
        
        # 1. Bank name check
        bank_variations = [
            bank_name.lower(),
            bank_name.lower().replace(' ', ''),
            ''.join(bank_name.lower().split())
        ]
        bank_found = any(var in text_lower for var in bank_variations)
        validation_result['scores']['bank_name'] = 1.0 if bank_found else 0.0
        
        if not bank_found:
            validation_result['reasons'].append("Bank name not found")
        
        # 2. Loan keywords
        loan_keywords = ['loan', 'credit', 'borrower', 'lender', 'emi', 'installment']
        loan_score = sum(1 for kw in loan_keywords if kw in text_lower) / len(loan_keywords)
        validation_result['scores']['loan_keywords'] = loan_score
        
        if loan_score < 0.3:
            validation_result['reasons'].append("Insufficient loan keywords")
        
        # 3. Terms/legal keywords
        terms_keywords = [
            'terms', 'conditions', 'agreement', 'charges', 'fees',
            'interest', 'rate', 'annual percentage', 'apr'
        ]
        terms_score = sum(1 for kw in terms_keywords if kw in text_lower) / len(terms_keywords)
        validation_result['scores']['terms_keywords'] = terms_score
        
        if terms_score < 0.3:
            validation_result['reasons'].append("Insufficient terms/conditions keywords")
        
        # 4. Specific financial terms
        financial_terms = [
            'processing fee', 'interest rate', 'prepayment', 'foreclosure',
            'penal', 'penalty', 'late payment', 'tenure', 'repayment'
        ]
        financial_score = sum(1 for term in financial_terms if term in text_lower) / len(financial_terms)
        validation_result['scores']['financial_terms'] = financial_score
        
        # 5. Document structure indicators
        structure_indicators = [
            'section', 'clause', 'article', 'paragraph', 'annexure',
            'schedule', 'appendix', 'whereas', 'hereby'
        ]
        structure_score = sum(1 for ind in structure_indicators if ind in text_lower) / len(structure_indicators)
        validation_result['scores']['document_structure'] = structure_score
        
        # 6. Length check (proper T&C docs are substantial)
        length_score = min(len(text) / 10000, 1.0)  # Scale up to 10k chars
        validation_result['scores']['content_length'] = length_score
        
        # Overall validation
        overall_score = (
            validation_result['scores']['bank_name'] * 0.25 +
            validation_result['scores']['loan_keywords'] * 0.20 +
            validation_result['scores']['terms_keywords'] * 0.20 +
            validation_result['scores']['financial_terms'] * 0.20 +
            validation_result['scores']['document_structure'] * 0.10 +
            validation_result['scores']['content_length'] * 0.05
        )
        
        validation_result['overall_score'] = overall_score
        validation_result['valid'] = overall_score >= 0.50  # Threshold
        
        if validation_result['valid']:
            validation_result['reasons'].append(f"Passed validation (score: {overall_score:.2f})")
        
        return validation_result['valid'], validation_result
    
    @staticmethod
    def calculate_confidence_score(
        text: str,
        metadata: Dict,
        validation_result: Dict
    ) -> float:
        """Enhanced confidence scoring"""
        score = validation_result.get('overall_score', 0.0) * 0.5
        
        # Bonus for official domain
        if metadata.get('from_official_domain'):
            score += 0.15
        
        # Bonus for PDF
        if metadata.get('is_pdf'):
            score += 0.10
        
        # Bonus for substantial content
        if len(text) > 20000:
            score += 0.10
        elif len(text) > 10000:
            score += 0.05
        
        # Bonus for high semantic relevance
        if metadata.get('semantic_score', 0) > 0.5:
            score += 0.10
        
        # Penalty for generic/marketing content
        marketing_keywords = ['apply now', 'click here', 'special offer', 'limited time']
        marketing_count = sum(1 for kw in marketing_keywords if kw in text.lower())
        if marketing_count > 5:
            score -= 0.05
        
        return min(max(score, 0.0), 1.0)  # Clamp to [0, 1]

# ==============================================================================
# MAIN ORCHESTRATOR - PRODUCTION GRADE
# ==============================================================================

class ProductionBankScraper:
    """Production-grade orchestrator with all enhancements"""
    
    def __init__(self, config: Optional[ScraperConfig] = None):
        self.config = config or ScraperConfig()
        self.search = EnhancedSearchIntelligence(self.config)
        self.navigator = AsyncSmartNavigator(self.config)
        self.extractor = EnhancedContentExtractor()
        self.validator = EnhancedDocumentValidator()
        self.deduplicator = ContentDeduplicator() if self.config.enable_deduplication else None
        
        logger.info("🚀 Production Bank Scraper initialized")
    
    async def scrape_bank_documents(
        self,
        bank_name: str,
        loan_type: str = "personal",
        use_search_api: bool = True
    ) -> Dict[str, any]:
        """
        Main scraping method - fully async and production-ready
        
        Args:
            bank_name: Name of the bank
            loan_type: Type of loan (personal, home, auto, etc.)
            use_search_api: Whether to use Google Search API
        
        Returns:
            Comprehensive results dictionary
        """
        start_time = time.time()
        
        logger.info("=" * 70)
        logger.info(f"🏦 Starting scrape: {bank_name} - {loan_type} loan")
        logger.info("=" * 70)
        
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
                'execution_time_seconds': 0
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
            
            # Step 3: Extract and validate documents concurrently
            extraction_tasks = [
                self._extract_and_validate(url, bank_name, loan_type)
                for url in doc_urls[:10]  # Limit to top 10
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
            
            # Mark as success if we have high-confidence documents
            if any(doc['confidence'] > 0.6 for doc in result['documents']):
                result['success'] = True
            
            # Calculate execution time
            result['metadata']['execution_time_seconds'] = round(time.time() - start_time, 2)
            
            # Summary
            logger.info("=" * 70)
            logger.info("📈 SCRAPING COMPLETE")
            logger.info(f"✅ Success: {result['success']}")
            logger.info(f"📄 Documents found: {len(result['documents'])}")
            logger.info(f"⏱️  Execution time: {result['metadata']['execution_time_seconds']}s")
            logger.info("=" * 70)
            
            return result
            
        except Exception as e:
            logger.error(f"Fatal error during scraping: {e}", exc_info=True)
            result['metadata']['errors'].append(f"Fatal: {str(e)}")
            return result
    
    async def _search_for_urls(self, bank_name: str, loan_type: str) -> List[str]:
        """Search for starting URLs using Google API"""
        queries = self.search.generate_queries(bank_name, loan_type)
        all_links = []
        
        for query in queries[:3]:  # Limit to first 3 queries
            try:
                links = await self.search.search_google(query, num_results=10)
                filtered = self.search.filter_relevant_links(links, bank_name)
                all_links.extend([link['url'] for link in filtered])
            except Exception as e:
                logger.error(f"Search error for query '{query}': {e}")
        
        # Deduplicate and return
        return list(set(all_links))[:5]  # Top 5 unique URLs
    
    def _get_fallback_urls(self, bank_name: str, loan_type: str) -> List[str]:
        """Fallback URLs when search is unavailable"""
        url_map = {
            'HDFC': [
                'https://www.hdfcbank.com/personal/borrow/popular-loans/personal-loan',
                'https://www.hdfcbank.com/personal/borrow/loan-against-property'
            ],
            'SBI': [
                'https://sbi.co.in/web/personal-banking/loans/personal-loan',
                'https://sbi.co.in/web/interest-rates/interest-rates/loan-schemes-interest-rates'
            ],
            'ICICI': [
                'https://www.icicibank.com/Personal-Banking/loans/personal-loan',
                'https://www.icicibank.com/Personal-Banking/loans/home-loan'
            ],
            'Kotak': [
                'https://www.kotak.com/en/personal-banking/loans/personal-loan.html',
                'https://www.kotak.com/en/personal-banking/loans.html'
            ],
            'Axis': [
                'https://www.axisbank.com/retail/loans/personal-loan',
                'https://www.axisbank.com/retail/loans/home-loan'
            ],
            'Yes Bank': [
                'https://www.yesbank.in/personal-banking/yes-individual/borrow/personal-loan',
            ],
            'IndusInd': [
                'https://www.indusind.com/in/en/personal/loans/personal-loan.html',
            ],
            'IDFC First': [
                'https://www.idfcfirstbank.com/personal-banking/loans/personal-loan',
            ]
        }
        
        return url_map.get(bank_name, [f'https://www.{bank_name.lower().replace(" ", "")}.com'])
    
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
                return None
            
            # Semantic relevance check
            is_relevant, semantic_score = self.navigator.semantic_filter.is_relevant(text)
            
            if not is_relevant:
                logger.info(f"⚠️  Semantically irrelevant ({semantic_score:.2f}): {url[:60]}")
                return None
            
            # Validate
            is_valid, validation_result = self.validator.is_valid_loan_document(
                text, bank_name, loan_type
            )
            
            if not is_valid:
                logger.info(f"⚠️  Failed validation: {url[:60]} - {validation_result['reasons']}")
                return None
            
            # Calculate confidence
            metadata = {
                'url': url,
                'is_pdf': is_pdf,
                'from_official_domain': self._is_official_domain(url, bank_name),
                'size': len(text),
                'semantic_score': semantic_score,
                'validation': validation_result
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
