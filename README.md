# Aerodrome cbBTC-USDC LP Analysis Tool

Analyzes Aerodrome LP positions on Base. Fetches data from Basescan or analyzes existing JSON.

## Features
- Fetches LP events from Base blockchain via Basescan API
- Gets actual pool prices from swap events at transaction blocks
- Calculates APR, TWR, XIRR, vs HODL comparison
- Works offline with pre-existing JSON data

## Why This Exists
XIRR fails with frequent rebalancing (20+ in short periods). This tool provides multiple metrics to properly evaluate LP performance.

## Installation
```bash
pip install requests
```

## Usage

### Fetch from Basescan
```bash
# Get API key from https://basescan.org/apis
export BASESCAN_API_KEY=your_key

# Fetch and analyze wallet
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0x982116545d53F954Ac348694CB1a8cF45269bBf0

# With block range
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0xYourWallet --start-block 35102089 --end-block 35867540

# Pass API key directly
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0xYourWallet --api-key YOUR_KEY
```

### Analyze Offline
```bash
# Example data included
python aerodrome_cbbtc_usdc_analyzer.py --data-file full_example_data.json

# Save fetched data for later
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0xWallet --api-key KEY --format json > data.json
python aerodrome_cbbtc_usdc_analyzer.py --data-file data.json
```

### Output Formats
```bash
# Default: summary
python aerodrome_cbbtc_usdc_analyzer.py --data-file data.json

# Detailed text
python aerodrome_cbbtc_usdc_analyzer.py --format text --data-file data.json

# JSON for piping
python aerodrome_cbbtc_usdc_analyzer.py --format json --data-file data.json
```

## Data Format
```json
{
  "actions": [
    {
      "timestamp": "2025-09-04T10:30:00+00:00",
      "event": "IncreaseLiquidity",
      "cbbtc": 0.00207616,
      "usdc": 1641.79,
      "cash_flow": -1840.15
    }
  ]
}
```

Required: `timestamp`, `event`, `cbbtc`, `usdc`, `cash_flow`
Events: `IncreaseLiquidity` or `DecreaseLiquidity`
Cash flow: negative = deposit, positive = withdrawal

## Example Output
```
EXECUTIVE SUMMARY
================
Wallet: 0x982116545d53F954Ac348694CB1a8cF45269bBf0
Period: 17 days

Capital: $1,840 → +$515 profit
Return: 28% (601% APR)
Activity: 21 rebalances
vs HODL: +$481

Returns:
  LP APR: 601%
  HODL APR: 40%
  Outperformance: +561% APR
```

## How It Works

When fetching (--fetch):
1. Gets LP events from pool contract
2. Fetches swap events to get actual pool prices at each block
3. Calculates USD values using sqrtPriceX96 from pool
4. Computes APR, TWR, vs HODL metrics

No external price feeds - everything from blockchain.

## Why XIRR Fails

With 20+ rebalances in short periods, XIRR produces misleading results (e.g., 57,990% for 601% APR strategy). Each rebalance looks like complete capital withdrawal/redeployment, causing extreme annualization.

Use APR instead - it's what DeFi platforms use.

## License
MIT