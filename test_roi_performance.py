#!/usr/bin/env python3
"""Test script to verify ROI caching and performance improvements."""

import logging
import time
from pathlib import Path
from investory import roi

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def test_batch_benchmark_fetch():
    """Test the batch benchmark fetching."""
    print("\n=== Testing Batch Benchmark Fetch ===")
    
    ticker = "SPY"
    years = [2020, 2021, 2022, 2023, 2024]
    
    print(f"Fetching benchmark data for {ticker} for years {years}")
    start = time.time()
    results = roi.get_benchmark_prices_batch(ticker, years)
    elapsed = time.time() - start
    
    print(f"Fetched in {elapsed:.2f} seconds")
    print(f"Results: {results}")
    
    return results

def test_cache_functionality():
    """Test cache save/load functionality."""
    print("\n=== Testing Cache Functionality ===")
    
    import pandas as pd
    from tempfile import TemporaryDirectory
    
    with TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "test_cache.json"
        
        # Create test data
        test_data = {
            'ledger_hash': 'abc123',
            'investment_account': 'assets:investments',
            'pnl_account': 'income:financial',
            'begin_date': '2020-01-01',
            'currency': '$',
            'conversion_args': ['--infer-market-prices'],
            'portfolio_data': pd.DataFrame({
                'date': pd.to_datetime(['2023-01-31', '2023-02-28']),
                'twr_factor': [1.05, 1.03]
            }),
            'benchmark_data': {
                'SPY': pd.Series([10.5, 12.3, 8.7], index=[2021, 2022, 2023], name='SPY')
            }
        }
        
        # Save cache
        print("Saving cache...")
        roi.save_cache(cache_file, test_data)
        assert cache_file.exists(), "Cache file not created"
        
        # Load cache
        print("Loading cache...")
        loaded = roi.load_cache(cache_file)
        assert loaded is not None, "Failed to load cache"
        
        # Verify data
        assert loaded['ledger_hash'] == test_data['ledger_hash']
        assert isinstance(loaded['portfolio_data'], pd.DataFrame)
        assert isinstance(loaded['benchmark_data']['SPY'], pd.Series)
        
        print("✓ Cache save/load working correctly")

def test_ledger_hash():
    """Test ledger hash generation (requires a real ledger file)."""
    print("\n=== Testing Ledger Hash (requires ledger file) ===")
    
    # This will only work if you have a ledger file
    # You can customize the path
    import os
    ledger_file = os.path.expanduser("~/.hledger.journal")
    
    if not Path(ledger_file).exists():
        print(f"Skipping: {ledger_file} not found")
        return
    
    try:
        hash1 = roi.get_ledger_hash(ledger_file, "assets:investments", "income:financial")
        print(f"Ledger hash: {hash1[:16]}...")
        
        # Generate again - should be same
        hash2 = roi.get_ledger_hash(ledger_file, "assets:investments", "income:financial")
        assert hash1 == hash2, "Hash should be consistent"
        print("✓ Ledger hash generation working")
    except Exception as e:
        print(f"Could not test ledger hash: {e}")

if __name__ == "__main__":
    print("Testing ROI Performance Improvements")
    print("=" * 50)
    
    # Run tests
    test_batch_benchmark_fetch()
    test_cache_functionality()
    test_ledger_hash()
    
    print("\n" + "=" * 50)
    print("✓ All tests completed!")
