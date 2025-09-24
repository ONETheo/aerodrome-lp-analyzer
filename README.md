# aerodrome-lp-analyzer

Analyzes Aerodrome LP positions on Base. Fetches data from Basescan or analyzes existing JSON.

## Installation
```bash
pip install requests
```

## Usage

### Fetch from Basescan
```bash
# Get API key from https://basescan.org/apis
export BASESCAN_API_KEY=your_key

# Analyze wallet
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0x982116545d53F954Ac348694CB1a8cF45269bBf0

# With block range
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0xWallet --start-block 35102089 --end-block 35867540
```

### Analyze Offline
```bash
# Use example data
python aerodrome_cbbtc_usdc_analyzer.py --data-file full_example_data.json

# Save fetched data for later
python aerodrome_cbbtc_usdc_analyzer.py --fetch 0xWallet --api-key KEY --format json > data.json
python aerodrome_cbbtc_usdc_analyzer.py --data-file data.json
```

## Output
```
Capital: $1,840 → +$515 profit
Return: 28% (601% APR)
Activity: 21 rebalances
vs HODL: +$481
```

## Why This Exists

XIRR fails with frequent rebalancing. This tool provides APR, TWR, and vs-HODL metrics that actually work.

## License
MIT
