"""
Easy-to-Use Bank Scraper Interface - MINIMAL VERSION
"""

import asyncio
import argparse
import json
from typing import List, Dict, Optional
from pathlib import Path

# Import the scraper
from bank_scraper_v2 import (
    ProductionBankScraper, 
    BatchScraper, 
    ScraperConfig,
    logger
)

# ==============================================================================
# SIMPLE PYTHON FUNCTIONS
# ==============================================================================

class EasyScraper:
    """Simple wrapper for easy queries"""
    
    def __init__(self, google_api_key: str = None, google_cse_id: str = None):
        self.config = ScraperConfig(
            google_api_key=google_api_key or "YOUR_API_KEY_HERE",
            google_cse_id=google_cse_id or "YOUR_CSE_ID_HERE"
        )
        self.scraper = ProductionBankScraper(self.config)
    
    def scrape_bank(
        self, 
        bank_name: str, 
        loan_type: str = "personal",
        save_results: bool = False
    ) -> Dict:
        """Scrape a single bank"""
        print(f"🔍 Scraping {bank_name} {loan_type} loan documents...")
        
        result = asyncio.run(
            self.scraper.scrape_bank_documents(
                bank_name, 
                loan_type,
                use_search_api=(self.config.google_api_key != "YOUR_API_KEY_HERE")
            )
        )
        
        if save_results and result['success']:
            self.scraper.save_results(result)
        
        return result
    
    def scrape_multiple_banks(
        self,
        banks: List[str],
        loan_type: str = "personal"
    ) -> Dict[str, Dict]:
        """Scrape multiple banks in parallel"""
        batch_scraper = BatchScraper(self.config)
        results = asyncio.run(
            batch_scraper.scrape_multiple_banks(banks, loan_type)
        )
        return results
    
    def get_best_document(self, result: Dict) -> Optional[Dict]:
        """Get the highest confidence document from results"""
        if not result.get('documents'):
            return None
        return max(result['documents'], key=lambda x: x['confidence'])

# ==============================================================================
# COMMAND LINE INTERFACE
# ==============================================================================

def create_cli():
    """Create command-line interface"""
    parser = argparse.ArgumentParser(
        description="🏦 Bank Document Scraper"
    )
    
    parser.add_argument('--bank', type=str, help='Bank name')
    parser.add_argument('--loan-type', type=str, default='personal')
    parser.add_argument('--api-key', type=str, help='Google API key')
    parser.add_argument('--cse-id', type=str, help='Google CSE ID')
    
    return parser

def run_cli():
    """Run the CLI"""
    parser = create_cli()
    args = parser.parse_args()
    
    if not args.bank:
        print("Error: --bank is required")
        return
    
    scraper = EasyScraper(
        google_api_key=args.api_key,
        google_cse_id=args.cse_id
    )
    
    result = scraper.scrape_bank(args.bank, args.loan_type)
    print(json.dumps(result, indent=2))

# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    run_cli()
