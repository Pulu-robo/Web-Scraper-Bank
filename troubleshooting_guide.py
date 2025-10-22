"""
TROUBLESHOOTING GUIDE
=====================

Common issues and solutions for the bank document scraper.
Reference this when debugging or tuning the system.
"""

class TroubleshootingGuide:
    """Interactive troubleshooting guide"""
    
    @staticmethod
    def print_guide():
        guide = """
╔══════════════════════════════════════════════════════════════════════╗
║                      TROUBLESHOOTING GUIDE                           ║
╚══════════════════════════════════════════════════════════════════════╝

📌 QUICK REFERENCE
==================

Issue Categories:
1. Wrong Bank Results
2. Wrong Loan Type Results  
3. Low Confidence Scores
4. Too Many Rejections
5. Slow Performance
6. API/Network Issues
7. Cache Issues
8. UI/Display Issues

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 1: Getting Wrong Bank Results
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- Documents from ICICI when searching for HDFC
- Multiple banks appearing in results
- Competing bank names in documents

DIAGNOSIS:
→ Check Step 11: Bank competition detection
→ Check Step 5: Bank verification in URL filtering
→ Check metadata: 'competing_banks_found'

SOLUTIONS:

A. Strengthen Bank Competition Check:
   Location: bank_scraper_v2.py - EnhancedDocumentValidator
   
   # Increase penalty for competing banks
   if competing_banks:
       reasons.append(f"Competing banks: {competing_banks}")
       return (False, "Competing banks detected")
   
   # Lower threshold for competing bank mentions
   threshold = 2  # Change from 3 to 2

B. Increase Official Domain Bonus:
   Location: bank_scraper_v2.py - calculate_confidence()
   
   # Boost official domain bonus
   if from_official:
       score += 0.25  # Change from 0.15 to 0.25

C. Add Bank Name Verification in URL:
   Location: bank_scraper_v2.py - filter_relevant_links()
   
   # Require bank name in URL for non-PDF
   if not url.endswith('.pdf'):
       if bank_name.lower() not in url.lower():
           continue  # Skip this URL

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 2: Getting Wrong Loan Type Results
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- Personal loan docs when searching for home loan
- Mixed loan types in results
- Loan type keywords don't match query

DIAGNOSIS:
→ Check Step 13: Loan type competition check
→ Check Step 7: Wrong loan type detection in URLs
→ Check LOAN_KEYWORDS configuration

SOLUTIONS:

A. Strengthen Loan Type Keywords:
   Location: bank_scraper_v2.py - LOAN_KEYWORDS
   
   # Add more specific keywords
   'personal': {
       'primary': ['personal-loan', 'personal_loan', 'personalloan', 'pl-'],
       'phrases': ['instant personal', 'pre-approved personal', 'quick personal'],
       'codes': ['/pl/', '/personal-loan/']
   }

B. Increase Loan Type Mention Threshold:
   Location: bank_scraper_v2.py - EnhancedDocumentValidator
   
   # Require more loan type mentions
   min_loan_mentions = 5  # Change from 3 to 5

C. Penalize Wrong Loan Type More:
   Location: bank_scraper_v2.py - wrong_loan_check()
   
   # If wrong loan type mentions > correct loan type
   if wrong_count > correct_count:
       return (False, f"Wrong loan type dominant")

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 3: Low Confidence Scores
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- All documents < 0.70 confidence
- Valid documents being rejected
- Average confidence too low

DIAGNOSIS:
→ Check Step 16-17: Confidence calculation
→ Check if official domain bonus applied
→ Check validation thresholds

SOLUTIONS:

A. Review Confidence Weights:
   Location: bank_scraper_v2.py - calculate_confidence()
   
   Components:
   - Semantic score (0.40 weight)
   - Bank mentions (0.20 weight)
   - Loan mentions (0.15 weight)
   - Official domain (0.15 bonus)
   - PDF bonus (0.10)
   
   Adjust if needed:
   score = (semantic * 0.40) + (bank_score * 0.25) + ...

B. Lower Validation Threshold:
   Location: bank_scraper_v2.py - ScraperConfig
   
   class ScraperConfig:
       min_confidence: float = 0.60  # Change from 0.65

C. Check Official Domain Detection:
   
   # Verify official domains list
   OFFICIAL_DOMAINS = {
       'hdfc': ['hdfcbank.com'],
       'sbi': ['sbi.co.in', 'onlinesbi.com'],
       ...
   }

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 4: Too Many Rejections
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- Most URLs rejected
- Few or no final documents
- High rejection count in metadata

DIAGNOSIS:
→ Check validation thresholds
→ Check semantic threshold (Step 21)
→ Check if blacklist too aggressive

SOLUTIONS:

A. Lower Semantic Threshold:
   Location: bank_scraper_v2.py - ScraperConfig
   
   semantic_threshold: float = 0.25  # Change from 0.30

B. Reduce Validation Requirements:
   Location: bank_scraper_v2.py - validate_document()
   
   # Lower minimum content length
   if len(text) < 200:  # Change from 300
       return (False, "Insufficient content")

C. Review Blacklist:
   Location: bank_scraper_v2.py - BLACKLIST
   
   # Remove overly broad terms
   BLACKLIST = {
       'decease', 'death',  # Keep critical terms
       # 'grievance',  # Remove if causing issues
   }

D. Adjust Loan Type Configuration:
   Location: bank_scraper_v2.py - ScraperConfig
   
   # Make loan type matching more flexible
   strict_loan_type_matching: bool = False

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 5: Slow Performance
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- Takes > 2 minutes per bank
- Hangs or times out
- High CPU/memory usage

DIAGNOSIS:
→ Check concurrent requests
→ Check if cache enabled (Step 29)
→ Check URL count

SOLUTIONS:

A. Enable Caching:
   Location: app.py
   
   # Make sure cache is enabled
   use_cache = st.checkbox('Use Cache (24h)', value=True)
   result = scraper.scrape_bank(bank, loan_type, use_cache=use_cache)

B. Implement URL Priority (Step 25):
   
   # Process high-priority URLs first
   # This is already implemented - verify it's working

C. Use Test Mode for Development:
   Location: app.py
   
   # Enable test mode for quick testing
   test_mode = st.checkbox('Enable Test Mode', value=True)
   # This limits to 3 URLs

D. Reduce Concurrent Requests:
   Location: bank_scraper_v2.py - ScraperConfig
   
   concurrent_requests: int = 3  # Change from 5

E. Implement Timeout:
   
   # Add timeout to requests
   timeout = aiohttp.ClientTimeout(total=30)

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 6: API/Network Issues
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- "API error" messages
- No results from search
- Network timeout errors

DIAGNOSIS:
→ Check API keys configured
→ Check API quota
→ Check network connectivity

SOLUTIONS:

A. Verify API Keys:
   
   # Check in Streamlit sidebar
   # Or set environment variables:
   export GOOGLE_API_KEY='your_key'
   export GOOGLE_CSE_ID='your_cse_id'

B. Check API Quota:
   
   # Google Custom Search: 100 queries/day free
   # Upgrade if needed: https://developers.google.com/custom-search

C. Implement Retry Logic:
   
   # Already implemented with tenacity
   # Verify retry decorator is working

D. Add Fallback URLs:
   
   # If API fails, use fallback URLs
   fallback_urls = [
       f'https://{bank_domain}/loans/{loan_type}'
   ]

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 7: Cache Issues
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- Cache not working
- Stale results returned
- Cache errors in logs

DIAGNOSIS:
→ Check Step 29 implementation
→ Check cache directory permissions
→ Check cache expiry settings

SOLUTIONS:

A. Clear Cache:
   Location: app.py
   
   # Use "Clear Cache" button in UI
   # Or manually:
   rm -rf cache/*.pkl

B. Verify Cache Directory:
   
   # Check if cache directory exists
   ls -la cache/
   
   # Fix permissions if needed
   chmod 755 cache/

C. Adjust Cache Expiry:
   Location: scraper_interface.py
   
   # Change max age
   def _get_from_cache(self, bank, loan_type, max_age_hours=48):
       # Changed from 24 to 48 hours

D. Disable Cache for Testing:
   
   # In UI, uncheck "Use Cache"
   # Or:
   result = scraper.scrape_bank(bank, loan_type, use_cache=False)

═══════════════════════════════════════════════════════════════════════

❌ PROBLEM 8: UI/Display Issues
═══════════════════════════════════════════════════════════════════════

SYMPTOMS:
- UI not loading
- Results not displaying
- Filters not working

DIAGNOSIS:
→ Check Step 23 implementation
→ Check Streamlit version
→ Check session state

SOLUTIONS:

A. Restart Streamlit:
   
   # Kill existing process
   lsof -ti:8501 | xargs kill -9
   
   # Start fresh
   streamlit run app.py

B. Clear Streamlit Cache:
   
   # In browser, press 'c' then 'Clear cache'
   # Or delete:
   rm -rf ~/.streamlit/

C. Check Streamlit Version:
   
   # Verify version
   streamlit --version
   
   # Update if needed
   pip install streamlit==1.31.0

D. Reset Session State:
   
   # In app.py, add reset button
   if st.button('Reset'):
       for key in list(st.session_state.keys()):
           del st.session_state[key]

═══════════════════════════════════════════════════════════════════════

📊 PERFORMANCE TUNING
═══════════════════════════════════════════════════════════════════════

FOR SPEED:
1. Enable cache (Step 29)
2. Enable test mode (3 URLs only)
3. Use URL priority (Step 25)
4. Reduce concurrent_requests

FOR ACCURACY:
1. Increase validation thresholds
2. Require official domain
3. Strengthen bank competition check
4. Increase minimum bank mentions

FOR RECALL (More Results):
1. Lower confidence threshold
2. Lower semantic threshold
3. Reduce validation requirements
4. Process more URLs

═══════════════════════════════════════════════════════════════════════

🔧 CONFIGURATION REFERENCE
═══════════════════════════════════════════════════════════════════════

Key Configuration Parameters (ScraperConfig):

min_confidence: 0.65           # Minimum confidence to accept document
semantic_threshold: 0.30       # Minimum semantic similarity
max_documents: 10              # Maximum documents to return
concurrent_requests: 5         # Parallel HTTP requests
enable_deduplication: True     # Remove duplicate content
strict_loan_type_matching: True  # Strict loan type validation

Adjust based on your needs!

═══════════════════════════════════════════════════════════════════════

📞 DEBUGGING TIPS
═══════════════════════════════════════════════════════════════════════

1. Enable Test Mode:
   - Limits to 3 URLs
   - Verbose logging
   - Faster iteration

2. Check Diagnostics Tab:
   - View metadata analysis
   - See rejection reasons
   - Inspect quality metrics

3. Review Logs:
   - Check console output
   - Look for WARNING/ERROR messages
   - Check rejection reasons

4. Use Cache Info:
   - Click "Cache Info" button
   - See what's cached
   - Clear if needed

5. Test Individual Components:
   - Run unit tests (test_step*.py)
   - Test single bank/loan type
   - Verify each step

═══════════════════════════════════════════════════════════════════════

✅ VERIFICATION CHECKLIST
═══════════════════════════════════════════════════════════════════════

Before Production:
□ All 5 integration tests pass
□ No cross-contamination detected
□ Average confidence > 0.70
□ Official domain % > 50%
□ Processing time < 2 minutes
□ Cache working correctly
□ UI responsive and clear
□ Error handling robust
□ API keys secured
□ Logging configured

═══════════════════════════════════════════════════════════════════════

Need more help?
- Review individual STEP implementations
- Check test files for examples
- Read inline code comments
- Run final_checklist.py

╚══════════════════════════════════════════════════════════════════════╝
"""
        print(guide)
    
    @staticmethod
    def get_solution(issue_type: str):
        """Get specific solution for an issue"""
        solutions = {
            'wrong_bank': """
Solution for Wrong Bank Results:
1. Check EnhancedDocumentValidator.validate_document()
2. Verify competing_banks_found in metadata
3. Increase official_domain_bonus in calculate_confidence()
4. Add bank name requirement in URL filtering
            """,
            'wrong_loan': """
Solution for Wrong Loan Type:
1. Review LOAN_KEYWORDS configuration
2. Increase min_loan_mentions threshold
3. Strengthen wrong_loan_check() penalties
4. Add loan type verification in URL filtering
            """,
            'low_confidence': """
Solution for Low Confidence:
1. Review confidence calculation weights
2. Lower min_confidence threshold
3. Verify official domain detection
4. Check semantic similarity scoring
            """,
            'too_many_rejections': """
Solution for Too Many Rejections:
1. Lower semantic_threshold
2. Reduce minimum content length
3. Review BLACKLIST for overly broad terms
4. Make loan type matching less strict
            """,
            'slow': """
Solution for Slow Performance:
1. Enable caching (Step 29)
2. Use test mode (3 URLs)
3. Implement URL priority (Step 25)
4. Reduce concurrent_requests
            """
        }
        
        return solutions.get(issue_type, "Issue type not found. See full guide.")


if __name__ == '__main__':
    guide = TroubleshootingGuide()
    guide.print_guide()
