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
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
import PyPDF2

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ==============================================================================
# IMPROVED SEMANTIC FILTER
# ==============================================================================

class ImprovedSemanticFilter:
    """Enhanced filter that prevents irrelevant results"""
    
    def __init__(self, loan_type: str = "personal"):
        self.loan_type = loan_type.lower()
        logger.info(f"🎯 Initialized filter for {loan_type} loans")
    
    def is_relevant(self, text: str, url: str = "", threshold: float = 0.25) -> Tuple[bool, float]:
        """Check relevance with negative keyword filtering"""
        if not text or len(text) < 100:
            return False, 0.0
        
        text_lower = text.lower()
        url_lower = url.lower()
        
        # CRITICAL: Exclude irrelevant pages
        negative_keywords = [
            'deceased', 'death', 'demise', 'claim', 'settlement',
            'insurance claim', 'nominee', 'legal heir',
            'credit card', 'debit card', 'savings account',
            'fixed deposit', 'recurring deposit',
            'complaint', 'grievance', 'customer care'
        ]
        
        for neg_kw in negative_keywords:
            if neg_kw in text_lower[:800] or neg_kw in url_lower:
                logger.info(f"❌ Filtered: '{neg_kw}' found in {url[:60]}")
                return False, 0.0
        
        # Positive scoring
        loan_keywords = [self.loan_type, 'loan', 'credit', 'borrower', 'lender']
        loan_score = sum(1 for kw in loan_keywords if kw in text_lower[:2000]) / len(loan_keywords)
        
        terms_keywords = [
            'terms and conditions', 'terms & conditions',
            'most important terms', 'mitc', 'agreement',
            'charges', 'fees', 'interest rate',
            'processing fee', 'emi', 'tenure',
            'prepayment', 'foreclosure'
        ]
        terms_score = sum(1 for kw in terms_keywords if kw in text_lower[:3000]) / len(terms_keywords)
        
        final_score = (loan_score * 0.4 + terms_score * 0.6)
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
        """Validate with strict checks"""
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
        
        # 2. Bank name
        bank_clean = bank_name.lower().replace(' ', '')
        text_clean = text_lower.replace(' ', '')
        bank_found = bank_clean in text_clean
        result['scores']['bank_name'] = 1.0 if bank_found else 0.0
        
        # 3. Loan type
        loan_found = loan_type.lower() in text_lower[:2000]
        result['scores']['loan_type'] = 1.0 if loan_found else 0.3
        
        # 4. Essential keywords
        essential = ['loan', 'interest', 'rate', 'charges', 'fees']
        essential_score = sum(1 for kw in essential if kw in text_lower) / len(essential)
        result['scores']['essential'] = essential_score
        
        # 5. Terms keywords
        terms = ['terms', 'conditions', 'agreement', 'tenure']
        terms_score = sum(1 for kw in terms if kw in text_lower) / len(terms)
        result['scores']['terms'] = terms_score
        
        # Overall score
        overall = (
            result['scores']['negative_check'] * 0.30 +
            result['scores']['bank_name'] * 0.20 +
            result['scores']['loan_type'] * 0.15 +
            result['scores']['essential'] * 0.20 +
            result['scores']['terms'] * 0.15
        )
        
        result['overall_score'] = overall
        result['valid'] = overall >= 0.60
        
        if not result['valid']:
            result['reasons'].append(f"Low validation score: {overall:.2f}")
        
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
        """Filter URLs aggressively"""
        exclude = [
            'deceased', 'claim', 'settlement', 'death',
            'credit-card', 'debit-card', 'savings',
            'complaints', 'customer-care', 'careers',
            'about-us', 'news', 'branch', 'atm'
        ]
        
        include = [loan_type.lower(), 'loan', 'terms', 'conditions', 'charges', 'pdf']
        
        filtered = []
        for link in links:
            url = link.get('url', '').lower()
            title = link.get('title', '').lower()
            combined = f"{url} {title}"
            
            # Skip if has negative patterns
            if any(pattern in combined for pattern in exclude):
                continue
            
            # Include if has enough positive patterns
            positive_count = sum(1 for pattern in include if pattern in combined)
            if positive_count >= 2:
                filtered.append(link['url'])
        
        return filtered

# ==============================================================================
# MAIN SCRAPER CLASS
# ==============================================================================

class FixedBankScraper:
    """Fixed scraper with all improvements"""
    
    def __init__(self, google_api_key: str, google_cse_id: str):
        self.api_key = google_api_key
        self.cse_id = google_cse_id
        self.search = ImprovedSearch()
        logger.info("✅ Fixed scraper initialized")
    
    async def scrape_bank(self, bank_name: str, loan_type: str) -> Dict:
        """Scrape with all fixes applied"""
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
        """Process a single URL"""
        try:
            logger.info(f"🔄 Processing: {url[:70]}")
            
            text = None
            is_pdf = url.lower().endswith('.pdf')
            
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
            if is_pdf:
                confidence += 0.10
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
    
    async def _extract_pdf(self, url: str) -> Optional[str]:
        """Extract text from PDF"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        return None
                    
                    pdf_content = await response.read()
                    pdf_file = BytesIO(pdf_content)
                    
                    reader = PyPDF2.PdfReader(pdf_file)
                    text = ""
                    for page in reader.pages[:100]:  # Max 100 pages
                        text += page.extract_text() + "\n"
                    
                    logger.info(f"   📄 PDF: {len(text)} chars")
                    return text
        except Exception as e:
            logger.error(f"   ❌ PDF extraction failed: {e}")
            return None
    
    async def _extract_html(self, url: str) -> Optional[str]:
        """Extract text from HTML"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=20, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }) as response:
                    if response.status != 200:
                        return None
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Remove unwanted elements
                    for element in soup(['script', 'style', 'nav', 'footer', 'header']):
                        element.decompose()
                    
                    text = soup.get_text()
                    logger.info(f"   🌐 HTML: {len(text)} chars")
                    return text
        except Exception as e:
            logger.error(f"   ❌ HTML extraction failed: {e}")
            return None

# ==============================================================================
# EASY INTERFACE (Compatible with existing code)
# ==============================================================================

class EasyScraper:
    """Drop-in replacement for your existing EasyScraper"""
    
    def __init__(self, google_api_key: str = None, google_cse_id: str = None):
        self.scraper = FixedBankScraper(
            google_api_key or "YOUR_API_KEY_HERE",
            google_cse_id or "YOUR_CSE_ID_HERE"
        )
        logger.info("🚀 EasyScraper initialized with fixes")
    
    def scrape_bank(
        self,
        bank_name: str,
        loan_type: str = "personal",
        save_results: bool = False
    ) -> Dict:
        """Scrape a single bank"""
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
