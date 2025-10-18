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
    
    tab1, tab2 = st.tabs(["Single Bank", "Multiple Banks"])
    
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
                    scraper = EasyScraper(final_api_key, final_cse_id)
                    result = scraper.scrape_bank(bank, loan_type, save_results=False)
                    
                    # Display results
                    if result['success']:
                        st.success(f"✅ Found {len(result['documents'])} documents!")
                        
                        # Metrics
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Documents", len(result['documents']))
                        col2.metric("Pages Crawled", result['metadata']['pages_crawled'])
                        col3.metric("Time", f"{result['metadata']['execution_time_seconds']}s")
                        
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
                    scraper = EasyScraper(final_api_key, final_cse_id)
                    
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

except ImportError as e:
    st.error("❌ Failed to import scraper module")
    st.exception(e)
    st.info("Make sure all dependencies are installed: `pip install -r requirements.txt`")
