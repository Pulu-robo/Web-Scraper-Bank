import streamlit as st
import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

st.set_page_config(
    page_title="Bank Document Scraper",
    page_icon="🏦",
    layout="wide"
)

st.title("🏦 Bank Loan Document Scraper")
st.markdown("Extract terms and conditions from bank websites")

# Load API keys from Streamlit secrets
try:
    api_key = st.secrets.get("GOOGLE_API_KEY", "")
    cse_id = st.secrets.get("GOOGLE_CSE_ID", "")
except:
    api_key = os.getenv("GOOGLE_API_KEY", "")
    cse_id = os.getenv("GOOGLE_CSE_ID", "")

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    
    api_key_input = st.text_input(
        "Google API Key", 
        value=api_key,
        type="password",
        help="Optional: Override secret"
    )
    cse_id_input = st.text_input(
        "Google CSE ID", 
        value=cse_id,
        type="password",
        help="Optional: Override secret"
    )
    
    # Use input if provided, otherwise use secrets
    final_api_key = api_key_input if api_key_input else api_key
    final_cse_id = cse_id_input if cse_id_input else cse_id
    
    st.markdown("---")
    
    # STEP 23: Filter Settings
    st.header("🎛️ Filter Settings")
    min_confidence = st.slider("Minimum Confidence", 0.0, 1.0, 0.65, 0.05,
                               help="Filter documents below this confidence score")
    official_only = st.checkbox("Official Domain Only", value=False,
                               help="Only show documents from official bank domains")
    pdf_only = st.checkbox("PDF Documents Only", value=False,
                          help="Only show PDF documents")
    min_bank_mentions = st.number_input("Min Bank Mentions", min_value=1, max_value=10, value=3,
                                       help="Minimum number of bank name mentions required")
    
    st.markdown("---")
    
    # STEP 31: Strictness Mode
    st.header("🎚️ Validation Strictness")
    strictness_mode = st.selectbox(
        "Strictness Level",
        options=["relaxed", "normal", "strict"],
        index=0,  # Default to relaxed
        help="""
        **Relaxed**: Min 1 bank mention, 1 loan mention, 40% confidence (More results)
        **Normal**: Min 2 bank mentions, 1 loan mention, 50% confidence (Balanced)
        **Strict**: Min 3 bank mentions, 2 loan mentions, 65% confidence (Fewer, higher quality)
        """
    )
    
    if strictness_mode == "relaxed":
        st.info("🔓 RELAXED: More results, may include some less relevant documents")
    elif strictness_mode == "normal":
        st.info("⚖️ NORMAL: Balanced - good mix of quantity and quality")
    else:
        st.info("🔒 STRICT: Fewer results, but highest quality and relevance")
    
    st.markdown("---")
    
    # STEP 27: Test Mode
    st.header("🧪 Test Mode")
    test_mode = st.checkbox("Enable Test Mode", value=False,
                           help="Quick testing: limit to 3 URLs, verbose logging")
    
    if test_mode:
        st.warning("⚠️ Test mode enabled: Limited to 3 URLs with verbose logging")
    
    st.markdown("---")
    
    # STEP 29: Cache Settings
    st.header("💾 Cache Settings")
    use_cache = st.checkbox("Use Cache (24h)", value=True,
                           help="Use cached results to speed up repeated searches")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Clear Cache", help="Clear all cached results"):
            try:
                # Create scraper to access cache
                temp_scraper = EasyScraper(final_api_key, final_cse_id, test_mode=False)
                if temp_scraper.cache:
                    count = temp_scraper.cache.clear()
                    st.success(f"✅ Cleared {count} cached results")
                else:
                    st.warning("Cache not available")
            except Exception as e:
                st.error(f"Error clearing cache: {e}")
    
    with col2:
        if st.button("Cache Info", help="Show cache statistics"):
            try:
                temp_scraper = EasyScraper(final_api_key, final_cse_id, test_mode=False)
                if temp_scraper.cache:
                    cache_info = temp_scraper.cache.get_cache_info()
                    st.info(f"📊 {cache_info['total_cached']} cached results in {cache_info['cache_dir']}")
                else:
                    st.warning("Cache not available")
            except Exception as e:
                st.error(f"Error getting cache info: {e}")
    
    st.markdown("---")
    st.markdown("### About")
    st.markdown("""
    This tool scrapes bank loan documents using:
    - 🔍 Smart search queries
    - 🎯 Content filtering
    - 📄 PDF extraction
    - ⚡ Async crawling
    """)
    
    st.info(f"API Keys: {'✅ Configured' if final_api_key else '❌ Not set'}")

# Main interface
try:
    from scraper_interface import EasyScraper
    
    # STEP 23: Changed from 2 tabs to 3 tabs (added Diagnostics)
    tab1, tab2, tab3 = st.tabs(["Single Bank", "Multiple Banks", "Diagnostics"])
    
    with tab1:
        st.header("Scrape Single Bank")
        
        col1, col2 = st.columns(2)
        
        with col1:
            bank = st.selectbox(
                "Select Bank",
                ["HDFC", "SBI", "ICICI", "Axis", "Kotak", "Yes Bank", "IndusInd", "IDFC First"]
            )
        
        with col2:
            loan_type = st.selectbox(
                "Loan Type",
                ["personal", "home", "auto", "education", "business"]
            )
        
        if st.button("🔍 Start Scraping", type="primary"):
            with st.spinner(f"Scraping {bank} {loan_type} loan documents..."):
                try:
                    # STEP 27: Pass test_mode to scraper
                    # STEP 31: Pass strictness_mode to scraper
                    scraper = EasyScraper(final_api_key, final_cse_id, test_mode=test_mode, strictness_mode=strictness_mode)
                    # STEP 29: Pass use_cache parameter
                    result = scraper.scrape_bank(bank, loan_type, save_results=False, use_cache=use_cache)
                    
                    # STEP 23: Store in session state for diagnostics tab
                    st.session_state.last_result = result
                    
                    # STEP 23: Apply filters to results
                    if result.get('success') and result.get('documents'):
                        original_count = len(result['documents'])
                        
                        # Filter by official domain
                        if official_only:
                            result['documents'] = [doc for doc in result['documents'] 
                                                   if doc.get('metadata', {}).get('from_official_domain', False)]
                        
                        # Filter by PDF only
                        if pdf_only:
                            result['documents'] = [doc for doc in result['documents'] 
                                                   if doc.get('metadata', {}).get('is_pdf', False)]
                        
                        # Filter by confidence
                        result['documents'] = [doc for doc in result['documents'] 
                                               if doc.get('confidence', 0) >= min_confidence]
                        
                        # Filter by bank mentions
                        result['documents'] = [doc for doc in result['documents'] 
                                               if doc.get('metadata', {}).get('bank_mention_count', 0) >= min_bank_mentions]
                        
                        filtered_count = len(result['documents'])
                        
                        # Show filter impact
                        if filtered_count < original_count:
                            st.info(f"🎯 Filters applied: {original_count} → {filtered_count} documents ({original_count - filtered_count} filtered out)")
                    
                    # Display results
                    if result['success']:
                        # STEP 29: Show if result is from cache
                        if result.get('metadata', {}).get('from_cache'):
                            st.info("💾 Result loaded from cache (speeds up repeated searches)")
                        
                        st.success(f"✅ Found {len(result['documents'])} documents!")
                        
                        # Metrics
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Documents", len(result['documents']))
                        col2.metric("Pages Crawled", result['metadata']['pages_crawled'])
                        col3.metric("Time", f"{result['metadata']['execution_time_seconds']}s")
                        
                        # STEP 28: Display Quality Metrics
                        if result.get('metadata', {}).get('quality_metrics'):
                            st.subheader("📊 Quality Metrics")
                            metrics = result['metadata']['quality_metrics']
                            
                            col1, col2, col3, col4 = st.columns(4)
                            
                            # Color-code quality score
                            quality_color = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
                            quality_icon = quality_color.get(metrics['quality_score'], "⚪")
                            
                            col1.metric("Quality Score", f"{quality_icon} {metrics['quality_score']}")
                            col2.metric("Avg Confidence", f"{metrics['average_confidence']:.1%}")
                            col3.metric("Official Domain", f"{metrics['official_domain_percentage']}%")
                            col4.metric("High Confidence", f"{metrics['high_confidence_count']}/{metrics['total_documents']}")
                            
                            # Additional metrics in expander
                            with st.expander("📈 Detailed Quality Metrics"):
                                col1, col2, col3 = st.columns(3)
                                col1.metric("PDF Documents", metrics['pdf_document_count'])
                                col2.metric("Avg Bank Mentions", f"{metrics['average_bank_mentions']:.1f}")
                                col3.metric("Cross-Contamination Risk", metrics['cross_contamination_risk_count'])
                        
                        # STEP 23: Show rejected URLs if available
                        if st.checkbox("Show Rejected URLs", key="show_rejected"):
                            st.subheader("❌ Rejected URLs")
                            
                            rejected_urls = result.get('metadata', {}).get('rejected_urls', [])
                            if rejected_urls:
                                st.write(f"Total rejections: {len(rejected_urls)}")
                                for rejection in rejected_urls:
                                    with st.expander(f"❌ {rejection.get('url', 'Unknown')[:60]}..."):
                                        st.write(f"**Reason:** {rejection.get('reason', 'Unknown')}")
                                        st.write(f"**Details:** {rejection.get('details', 'N/A')}")
                            else:
                                st.info("No rejection data available")
                        
                        # Display documents
                        for i, doc in enumerate(result['documents'], 1):
                            with st.expander(f"📄 Document {i} - Confidence: {doc['confidence']:.0%}"):
                                st.markdown(f"**URL:** {doc['url']}")
                                st.markdown(f"**Type:** {'PDF' if doc['metadata']['is_pdf'] else 'HTML'}")
                                st.markdown(f"**Size:** {doc['text_length']:,} characters")
                                
                                # Download button
                                st.download_button(
                                    label="📥 Download Full Text",
                                    data=doc['text'],
                                    file_name=f"{bank}_{loan_type}_doc_{i}.txt",
                                    mime="text/plain"
                                )
                                
                                st.text_area("Preview", doc['text'][:1000], height=200, key=f"doc_{i}")
                    else:
                        st.error("❌ Scraping failed")
                        if result['metadata'].get('errors'):
                            st.error("Errors:")
                            for error in result['metadata']['errors'][:5]:
                                st.text(f"- {error}")
                                
                except Exception as e:
                    st.error(f"Error: {str(e)}")
                    st.exception(e)
    
    with tab2:
        st.header("Batch Scrape Multiple Banks")
        
        banks = st.multiselect(
            "Select Banks",
            ["HDFC", "SBI", "ICICI", "Axis", "Kotak", "Yes Bank", "IndusInd", "IDFC First"],
            default=["HDFC", "SBI"]
        )
        
        loan_type_batch = st.selectbox(
            "Loan Type",
            ["personal", "home", "auto", "education", "business"],
            key="batch_loan_type"
        )
        
        if st.button("🔍 Start Batch Scraping", type="primary"):
            if not banks:
                st.warning("Please select at least one bank")
            else:
                try:
                    # STEP 31: Pass strictness mode to batch scraper
                    scraper = EasyScraper(final_api_key, final_cse_id, test_mode=test_mode, strictness_mode=strictness_mode)
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    results = scraper.scrape_multiple_banks(banks, loan_type_batch)
                    
                    progress_bar.progress(100)
                    st.success("✅ Batch scraping complete!")
                    
                    # Summary table
                    summary_data = []
                    for bank, result in results.items():
                        summary_data.append({
                            "Bank": bank,
                            "Status": "✅" if result.get('success') else "❌",
                            "Documents": len(result.get('documents', [])),
                            "Time (s)": result.get('metadata', {}).get('execution_time_seconds', 0)
                        })
                    
                    st.table(summary_data)
                    
                    # Detailed results
                    for bank, result in results.items():
                        with st.expander(f"🏦 {bank} - {'✅ Success' if result.get('success') else '❌ Failed'}"):
                            if result.get('success') and result.get('documents'):
                                for i, doc in enumerate(result['documents'][:3], 1):
                                    st.markdown(f"**Document {i}**")
                                    st.markdown(f"- URL: {doc['url']}")
                                    st.markdown(f"- Confidence: {doc['confidence']:.0%}")
                                    st.download_button(
                                        label=f"📥 Download Doc {i}",
                                        data=doc['text'],
                                        file_name=f"{bank}_{loan_type_batch}_doc_{i}.txt",
                                        mime="text/plain",
                                        key=f"batch_{bank}_{i}"
                                    )
                            else:
                                st.write("No documents found or scraping failed")
                                
                except Exception as e:
                    st.error(f"Batch error: {str(e)}")
                    st.exception(e)
    
    # STEP 23: Diagnostics Tab
    with tab3:
        st.header("🔬 Diagnostics & Debugging")
        st.info("Run a scrape in 'Single Bank' tab first, then return here to see detailed diagnostics")
        
        # Store result in session state for diagnostics
        if 'last_result' not in st.session_state:
            st.session_state.last_result = None
        
        # This will be populated when we modify the scraping code
        if st.session_state.last_result and st.session_state.last_result.get('documents'):
            result = st.session_state.last_result
            
            st.subheader("📊 Metadata Analysis")
            
            # Overall statistics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Documents", len(result['documents']))
            with col2:
                avg_conf = sum(d['confidence'] for d in result['documents']) / len(result['documents'])
                st.metric("Avg Confidence", f"{avg_conf:.1%}")
            with col3:
                pdf_count = sum(1 for d in result['documents'] if d.get('metadata', {}).get('is_pdf'))
                st.metric("PDF Documents", pdf_count)
            with col4:
                official_count = sum(1 for d in result['documents'] if d.get('metadata', {}).get('from_official_domain'))
                st.metric("Official Domain", official_count)
            
            # Per-document metadata
            st.subheader("📄 Per-Document Metadata")
            for i, doc in enumerate(result['documents'], 1):
                with st.expander(f"Document {i} - {doc.get('url', '')[:60]}..."):
                    
                    # Display all metadata as JSON
                    st.json(doc.get('metadata', {}))
                    
                    # Key metrics
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Bank Mentions", doc.get('metadata', {}).get('bank_mention_count', 0))
                    with col2:
                        density = doc.get('metadata', {}).get('bank_mention_density', 0)
                        st.metric("Bank Density", f"{density:.2f}/1K chars")
                    with col3:
                        loan_mentions = doc.get('metadata', {}).get('loan_type_mentions', 0)
                        st.metric("Loan Type Mentions", loan_mentions)
                    
                    # Competing banks
                    competing_banks = doc.get('metadata', {}).get('competing_banks_found', {})
                    if competing_banks:
                        st.write("⚠️ **Competing Banks Found:**")
                        st.json(competing_banks)
                    else:
                        st.success("✅ No competing banks detected")
                    
                    # Confidence breakdown
                    st.write("**Confidence Score:** {:.1%}".format(doc.get('confidence', 0)))
            
            # Rejected URLs section
            st.subheader("❌ Rejected URLs Analysis")
            rejected_urls = result.get('metadata', {}).get('rejected_urls', [])
            
            if rejected_urls:
                st.write(f"**Total Rejections:** {len(rejected_urls)}")
                
                # Group by reason
                reasons = {}
                for rej in rejected_urls:
                    reason = rej.get('reason', 'Unknown')
                    reasons[reason] = reasons.get(reason, 0) + 1
                
                st.write("**Rejection Reasons:**")
                for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
                    st.write(f"- {reason}: {count}")
                
                # Show individual rejections
                st.write("**Individual Rejections:**")
                for rejection in rejected_urls[:10]:  # Show first 10
                    with st.expander(f"❌ {rejection.get('url', 'Unknown')[:60]}..."):
                        st.write(f"**Reason:** {rejection.get('reason', 'Unknown')}")
                        st.write(f"**Details:** {rejection.get('details', 'N/A')}")
                        
                if len(rejected_urls) > 10:
                    st.info(f"Showing 10 of {len(rejected_urls)} rejections")
            else:
                st.info("No rejection data available for this scrape")
        else:
            st.warning("No scrape data available. Run a scrape in the 'Single Bank' tab first.")

except ImportError as e:
    st.error("❌ Failed to import scraper module")
    st.exception(e)
    st.info("Make sure all dependencies are installed: `pip install -r requirements.txt`")
