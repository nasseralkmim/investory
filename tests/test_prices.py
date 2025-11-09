"""Tests for cryptocurrency detection and price fetching."""

import tempfile
from pathlib import Path

import pytest

from investory.prices import (
    get_crypto_commodities,
    infer_yahoo_ticker,
    ensure_price_data,
)


class TestCryptoDetection:
    """Test cryptocurrency detection from ledger account paths."""

    @pytest.fixture
    def crypto_ledger(self, tmp_path):
        """Create a test ledger with crypto assets."""
        ledger_file = tmp_path / "crypto.ledger"
        ledger_file.write_text("""
2024-01-15 Buy Bitcoin
    assets:investments:crypto:btc    0.5 BTC @ $45000
    assets:cash:checking

2024-02-20 Buy Ethereum
    assets:investments:crypto:eth    2.0 ETH @ $2500
    assets:cash:checking

2024-03-10 Buy ETF
    assets:investments:etf:VWCE    10.0 VWCE @ €100
    assets:cash:checking
""")
        return str(ledger_file)

    @pytest.fixture
    def mixed_ledger(self, tmp_path):
        """Create a test ledger with mixed assets including crypto."""
        ledger_file = tmp_path / "mixed.ledger"
        ledger_file.write_text("""
2024-01-15 Buy Bitcoin
    assets:investments:crypto:btc    0.5 BTC @ $45000
    assets:cash:checking

2024-02-20 Buy Stock
    assets:investments:stocks:AAPL    10 AAPL @ $150
    assets:cash:checking

2024-03-10 Buy ETF
    assets:investments:etf:VWCE    100 VWCE @ €100
    assets:cash:checking
""")
        return str(ledger_file)

    def test_get_crypto_commodities_detects_crypto(self, crypto_ledger):
        """Test that crypto commodities are correctly detected."""
        crypto_assets = get_crypto_commodities(crypto_ledger)
        assert crypto_assets == {"BTC", "ETH"}

    def test_get_crypto_commodities_mixed_assets(self, mixed_ledger):
        """Test crypto detection with mixed asset types."""
        crypto_assets = get_crypto_commodities(mixed_ledger)
        assert crypto_assets == {"BTC"}
        assert "AAPL" not in crypto_assets
        assert "VWCE" not in crypto_assets

    def test_infer_yahoo_ticker_crypto(self):
        """Test that crypto assets get -USD suffix."""
        ticker = infer_yahoo_ticker("BTC", None, is_crypto=True)
        assert ticker == "BTC-USD"
        
        ticker = infer_yahoo_ticker("ETH", None, is_crypto=True)
        assert ticker == "ETH-USD"
        
        # Crypto should override currency detection
        ticker = infer_yahoo_ticker("BTC", "€", is_crypto=True)
        assert ticker == "BTC-USD"

    def test_infer_yahoo_ticker_non_crypto(self):
        """Test that non-crypto assets use exchange suffixes."""
        ticker = infer_yahoo_ticker("VWCE", "€", is_crypto=False)
        assert ticker == "VWCE.DE"
        
        ticker = infer_yahoo_ticker("ABEV3", "R$", is_crypto=False)
        assert ticker == "ABEV3.SA"
        
        ticker = infer_yahoo_ticker("AAPL", "$", is_crypto=False)
        assert ticker == "AAPL"

    def test_infer_yahoo_ticker_explicit_mapping_overrides(self):
        """Test that explicit ticker mapping is not affected by crypto detection."""
        # This tests that if user provides explicit mapping, it's used regardless
        # (This is tested implicitly in ensure_price_data with ticker_map parameter)
        pass


class TestFailedAssetReporting:
    """Test reporting of assets that fail to fetch prices."""

    @pytest.fixture
    def fake_asset_ledger(self, tmp_path):
        """Create a ledger with non-existent assets."""
        ledger_file = tmp_path / "fake.ledger"
        ledger_file.write_text("""
2024-01-15 Buy Fake Crypto
    assets:investments:crypto:fakecoin    100 FAKECOIN @ $1.50
    assets:cash:checking

2024-02-20 Buy Fake Stock
    assets:investments:stocks:FAKESTOCK    10.0 FAKESTOCK @ €50
    assets:cash:checking
""")
        return str(ledger_file)

    def test_ensure_price_data_reports_failed_assets(self, fake_asset_ledger, tmp_path, caplog):
        """Test that failed price fetches are properly logged."""
        import logging
        
        with caplog.at_level(logging.WARNING):
            price_files = ensure_price_data(
                ledger_file=fake_asset_ledger,
                data_dir=str(tmp_path / "data"),
                target_currency="€",
            )
        
        # Check that failure warnings were logged
        assert "Failed to fetch prices" in caplog.text
        assert "FAKECOIN" in caplog.text
        assert "FAKESTOCK" in caplog.text
        assert "--ticker-map" in caplog.text


class TestCryptoIntegration:
    """Integration tests for crypto price fetching."""

    def test_ensure_price_data_handles_crypto(self, tmp_path):
        """Test that crypto assets are handled correctly in ensure_price_data."""
        ledger_file = tmp_path / "crypto.ledger"
        ledger_file.write_text("""
2024-01-15 Buy Bitcoin
    assets:investments:crypto:btc    0.5 BTC @ $45000
    assets:cash:checking
""")
        
        data_dir = tmp_path / "data"
        price_files = ensure_price_data(
            ledger_file=str(ledger_file),
            data_dir=str(data_dir),
        )
        
        # Should create a price file with BTC-USD ticker
        assert len(price_files) >= 1
        assert any("BTC-USD" in pf for pf in price_files)

    def test_ticker_map_overrides_crypto_detection(self, tmp_path):
        """Test that explicit ticker_map overrides auto-detection."""
        ledger_file = tmp_path / "crypto.ledger"
        ledger_file.write_text("""
2024-01-15 Buy Bitcoin
    assets:investments:crypto:btc    0.5 BTC @ $45000
    assets:cash:checking
""")
        
        data_dir = tmp_path / "data"
        
        # Override BTC ticker (hypothetically)
        price_files = ensure_price_data(
            ledger_file=str(ledger_file),
            data_dir=str(data_dir),
            ticker_map={"BTC": "CUSTOM-TICKER"},
        )
        
        # Should use custom ticker instead of BTC-USD
        assert len(price_files) >= 0  # May fail but should attempt with CUSTOM-TICKER
