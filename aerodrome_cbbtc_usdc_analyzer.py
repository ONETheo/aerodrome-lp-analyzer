import argparse
import json
import sys
import os
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Dict, List, Tuple, Optional

getcontext().prec = 50

@dataclass
class LPMetrics:
    """Performance metrics for LP position analysis"""
    wallet: str
    blocks: str
    initial_capital: Decimal
    final_capital: Decimal
    net_profit: Decimal
    xirr: Optional[Decimal]
    twr: Decimal
    apr: Decimal
    apy: Decimal
    divergence_loss: Decimal
    vs_hodl: Decimal
    vs_hodl_apr: Decimal
    hodl_apr: Decimal
    rebalance_count: int
    days_active: int
    btc_price_start: Decimal
    btc_price_end: Decimal
    
class AerodromeAnalyzer:
    """Analyzes Aerodrome LP positions from transaction data"""

    def __init__(self, data_file: Path, wallet_address: str = None):
        self.data_file = data_file
        self.data = self._load_data()
        self.actions = self.data['actions']
        self.wallet_address = wallet_address
        self.btc_prices = self._extract_btc_prices()
        
    def _load_data(self) -> Dict:
        """Load transaction data from JSON file"""
        with open(self.data_file, 'r') as f:
            return json.load(f)
    
    def _extract_btc_prices(self) -> Dict[str, Decimal]:
        """Extract implied BTC prices from transaction cash flows"""
        prices = []
        
        for action in self.actions:
            cbbtc = Decimal(str(action['cbbtc']))
            usdc = Decimal(str(action['usdc']))
            cash_flow = abs(Decimal(str(action['cash_flow'])))
            
            if cbbtc > 0:
                implied_price = (cash_flow - usdc) / cbbtc
                prices.append(implied_price)
        
        if prices:
            return {
                'first': prices[0],
                'last': prices[-1],
                'average': sum(prices) / len(prices)
            }
        
        raise ValueError("Cannot extract BTC prices from transaction data. Ensure 'cbbtc' values are non-zero.")
    
    def analyze(self) -> LPMetrics:
        """Perform complete analysis of LP position"""
        dates = self._get_date_range()
        tokens = self._calculate_token_flows()
        cash_flows = self._calculate_cash_flows()
        rebalances = self._count_rebalances()
        xirr = self._calculate_xirr()
        twr = self._calculate_twr()
        apr, apy = self._calculate_apr_apy(cash_flows, dates['days'])
        divergence_loss = self._calculate_divergence_loss(tokens)
        vs_hodl = self._calculate_vs_hodl(tokens, cash_flows)
        hodl_apr, vs_hodl_apr = self._calculate_hodl_metrics(tokens, cash_flows, dates['days'])

        wallet = self._extract_wallet()
        blocks = self._extract_block_range()

        return LPMetrics(
            wallet=wallet,
            blocks=blocks,
            initial_capital=cash_flows['initial'],
            final_capital=cash_flows['total_withdrawn'],
            net_profit=cash_flows['net'],
            xirr=xirr,
            twr=twr,
            apr=apr,
            apy=apy,
            divergence_loss=divergence_loss,
            vs_hodl=vs_hodl,
            vs_hodl_apr=vs_hodl_apr,
            hodl_apr=hodl_apr,
            rebalance_count=rebalances,
            days_active=dates['days'],
            btc_price_start=self.btc_prices['first'],
            btc_price_end=self.btc_prices['last']
        )
    
    def _get_date_range(self) -> Dict:
        """Calculate date range of activity"""
        first = datetime.fromisoformat(self.actions[0]['timestamp'].replace('+00:00', ''))
        last = datetime.fromisoformat(self.actions[-1]['timestamp'].replace('+00:00', ''))
        return {
            'first': first,
            'last': last,
            'days': (last - first).days or 1
        }
    
    def _calculate_token_flows(self) -> Dict[str, Decimal]:
        """Calculate net token movements"""
        flows = {
            'cbbtc_in': Decimal('0'),
            'usdc_in': Decimal('0'),
            'cbbtc_out': Decimal('0'),
            'usdc_out': Decimal('0'),
            'cbbtc_fees': Decimal('0'),
            'usdc_fees': Decimal('0')
        }

        for action in self.actions:
            cbbtc = Decimal(str(action['cbbtc']))
            usdc = Decimal(str(action['usdc']))

            if action['event'] in ['Mint', 'IncreaseLiquidity']:
                flows['cbbtc_in'] += cbbtc
                flows['usdc_in'] += usdc
            elif action['event'] in ['Burn', 'DecreaseLiquidity']:
                flows['cbbtc_out'] += cbbtc
                flows['usdc_out'] += usdc
            elif action['event'] == 'Collect':
                flows['cbbtc_fees'] += cbbtc
                flows['usdc_fees'] += usdc

        flows['cbbtc_net'] = flows['cbbtc_out'] - flows['cbbtc_in']
        flows['usdc_net'] = flows['usdc_out'] - flows['usdc_in']
        return flows
    
    def _calculate_cash_flows(self) -> Dict[str, Decimal]:
        """Calculate USD cash flows"""
        first = self.actions[0]
        initial = abs(Decimal(str(first['cash_flow'])))
        
        deployed = sum(
            abs(Decimal(str(a['cash_flow'])))
            for a in self.actions
            if a['event'] in ['Mint', 'IncreaseLiquidity']
        )

        withdrawn = sum(
            Decimal(str(a['cash_flow']))
            for a in self.actions
            if a['event'] in ['Burn', 'DecreaseLiquidity']
        )

        fees_collected = sum(
            Decimal(str(a['cash_flow']))
            for a in self.actions
            if a['event'] == 'Collect'
        )

        withdrawn += fees_collected
        
        return {
            'initial': initial,
            'total_deployed': deployed,
            'total_withdrawn': withdrawn,
            'net': withdrawn - deployed
        }
    
    def _calculate_xirr(self) -> Optional[Decimal]:
        """Calculate XIRR using binary search"""
        try:
            cash_flows = []
            dates = []
            for action in self.actions:
                cf = Decimal(str(action['cash_flow']))
                dt = datetime.fromisoformat(action['timestamp'].replace('+00:00', ''))
                cash_flows.append(cf)
                dates.append(dt)

            if not cash_flows or len(cash_flows) < 2:
                return None

            def npv(rate):
                first_date = dates[0]
                total = Decimal(0)
                for cf, dt in zip(cash_flows, dates):
                    days = Decimal((dt - first_date).days)
                    if rate <= -1:
                        return float('inf') if cf < 0 else float('-inf')
                    total += cf / ((1 + rate) ** (days / Decimal(365)))
                return total

            low = Decimal('-0.999')
            high = Decimal('1000')

            npv_low = npv(low)
            npv_high = npv(high)

            if npv_low * npv_high > 0:
                for test_high in [100, 500, 1000, 5000, 10000, 50000]:
                    high = Decimal(str(test_high))
                    npv_high = npv(high)
                    if npv_low * npv_high < 0:
                        break

                if npv_low * npv_high > 0:
                    for test_low in [-0.5, -0.9, -0.95, -0.99, -0.995, -0.999]:
                        low = Decimal(str(test_low))
                        npv_low = npv(low)
                        if npv_low * npv_high < 0:
                            break

                if npv_low * npv_high > 0:
                    return None

            tolerance = Decimal('0.01')
            max_iterations = 200

            for i in range(max_iterations):
                if abs(high - low) < Decimal('0.000001'):
                    break

                mid = (low + high) / 2
                npv_mid = npv(mid)

                if abs(npv_mid) < tolerance:
                    return mid * 100

                if npv_low * npv_mid < 0:
                    high = mid
                    npv_high = npv_mid
                else:
                    low = mid
                    npv_low = npv_mid

            final_rate = (low + high) / 2
            final_npv = npv(final_rate)

            if abs(final_npv) < Decimal('100'):
                return final_rate * 100

            return None

        except Exception:
            return None

    def _count_rebalances(self) -> int:
        """Count rebalancing events (decrease followed by increase within 5 min)"""
        count = 0
        for i in range(len(self.actions) - 1):
            if (self.actions[i]['event'] in ['DecreaseLiquidity', 'Burn'] and
                self.actions[i+1]['event'] in ['IncreaseLiquidity', 'Mint']):
                
                curr_ts = datetime.fromisoformat(self.actions[i]['timestamp'].replace('+00:00', ''))
                next_ts = datetime.fromisoformat(self.actions[i+1]['timestamp'].replace('+00:00', ''))
                
                if (next_ts - curr_ts).total_seconds() < 300:
                    count += 1
        return count
    
    def _calculate_twr(self) -> Decimal:
        """Calculate Time-Weighted Return (ignores cash flow timing)"""
        period_returns = []
        
        for i in range(len(self.actions) - 1):
            if (self.actions[i]['event'] in ['DecreaseLiquidity', 'Burn'] and
                self.actions[i+1]['event'] in ['IncreaseLiquidity', 'Mint']):
                
                withdrawn = Decimal(str(self.actions[i]['cash_flow']))
                redeployed = abs(Decimal(str(self.actions[i+1]['cash_flow'])))
                
                if redeployed > 0:
                    period_return = (withdrawn - redeployed) / redeployed
                    period_returns.append(period_return)
        
        twr = Decimal('1.0')
        for r in period_returns:
            twr *= (1 + r)
        
        return (twr - 1) * 100
    
    def _calculate_apr_apy(self, cash_flows: Dict, days: int) -> Tuple[Decimal, Decimal]:
        """Calculate APR and APY"""
        if cash_flows['initial'] == 0 or days == 0:
            return Decimal('0'), Decimal('0')
        
        total_return = cash_flows['net'] / cash_flows['initial']
        daily_return = total_return / days
        
        apr = daily_return * 365 * 100
        apy = ((1 + daily_return) ** 365 - 1) * 100
        
        return apr, apy
    
    def _calculate_divergence_loss(self, tokens: Dict) -> Decimal:
        """Calculate divergence/impermanent loss"""
        last_btc_price = self.btc_prices['last']
        
        btc_lost_value = abs(tokens['cbbtc_net']) * last_btc_price
        return tokens['usdc_net'] - btc_lost_value
    
    def _calculate_vs_hodl(self, tokens: Dict, cash_flows: Dict) -> Decimal:
        """Calculate performance vs buy-and-hold"""
        first_price = self.btc_prices['first']
        last_price = self.btc_prices['last']
        
        first = self.actions[0]
        initial_cbbtc = Decimal(str(first['cbbtc']))
        initial_usdc = Decimal(str(first['usdc']))
        
        initial_value = initial_cbbtc * first_price + initial_usdc
        hodl_value = initial_cbbtc * last_price + initial_usdc
        hodl_return = hodl_value - initial_value
        
        lp_return = cash_flows['net']
        
        return lp_return - hodl_return

    def _calculate_hodl_metrics(self, tokens: Dict, cash_flows: Dict, days: int) -> Tuple[Decimal, Decimal]:
        """Calculate HODL APR and vs HODL APR difference"""
        if days == 0:
            return Decimal('0'), Decimal('0')

        first = self.actions[0]
        initial_cbbtc = Decimal(str(first['cbbtc']))
        initial_usdc = Decimal(str(first['usdc']))

        first_price = self.btc_prices['first']
        last_price = self.btc_prices['last']

        initial_value = initial_cbbtc * first_price + initial_usdc
        hodl_value = initial_cbbtc * last_price + initial_usdc
        hodl_return = hodl_value - initial_value

        if initial_value > 0:
            hodl_return_pct = hodl_return / initial_value
            hodl_apr = (hodl_return_pct / days) * 365 * 100
        else:
            hodl_apr = Decimal('0')

        lp_apr = (cash_flows['net'] / cash_flows['initial'] / days) * 365 * 100 if cash_flows['initial'] > 0 else Decimal('0')

        vs_hodl_apr = lp_apr - hodl_apr

        return hodl_apr, vs_hodl_apr

    def _extract_wallet(self) -> str:
        """Extract wallet address from data or use generic"""
        if 'wallet' in self.data:
            return self.data['wallet']

        if self.wallet_address:
            return self.wallet_address

        return "LP Position"

    def _extract_block_range(self) -> str:
        """Extract block range from data or calculate from timestamps"""
        if 'start_block' in self.data and 'end_block' in self.data:
            return f"{self.data['start_block']}-{self.data['end_block']}"

        if 'summary' in self.data:
            summary = self.data['summary']
            if 'start_block' in summary and 'end_block' in summary:
                return f"{summary['start_block']}-{summary['end_block']}"

        dates = self._get_date_range()
        start_date = dates['first'].strftime('%Y-%m-%d')
        end_date = dates['last'].strftime('%Y-%m-%d')
        return f"{start_date} to {end_date}"

def get_pool_price_from_swap_logs(block_number: int, api_key: str) -> Optional[Decimal]:
    """Get cbBTC-USDC pool's actual price from Swap events at specific block"""
    try:
        import requests
    except ImportError:
        print("ERROR: requests library not available for fetching pool prices")
        return None

    BASE_URL = "https://api.basescan.org/api"
    POOL_ADDRESS = "0x4e962BB3889Bf030368F56810A9c96B83CB3E778"  # cbBTC-USDC pool
    SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

    params = {
        "module": "logs",
        "action": "getLogs",
        "address": POOL_ADDRESS,
        "fromBlock": max(block_number - 100, 0),  # Look back further for swaps
        "toBlock": block_number,
        "topic0": SWAP_TOPIC,
        "apikey": api_key
    }

    try:
        response = requests.get(BASE_URL, params=params)

        if response.status_code != 200:
            print(f"  API error: HTTP {response.status_code}")
            return None

        data = response.json()

        if data.get("status") != "1":
            if "rate limit" in data.get("message", "").lower():
                print("  API rate limit reached")
            else:
                print(f"  API response: {data.get('message', 'Unknown error')}")
            return None

        if not data.get("result"):
            print(f"  No swap events found near block {block_number}")
            return None

        for log in reversed(data["result"]):
            data_hex = log.get("data", "")[2:]
            if len(data_hex) >= 256:
                try:
                    sqrt_price_x96 = int(data_hex[128:192], 16)
                    if sqrt_price_x96 > 0:
                        price_ratio = (sqrt_price_x96 / (2**96)) ** 2
                        btc_price_in_usdc = 1 / price_ratio if price_ratio > 0 else None
                        if btc_price_in_usdc and 10000 < btc_price_in_usdc < 1000000:
                            return Decimal(str(btc_price_in_usdc))
                except Exception as e:
                    continue

        print(f"  Could not extract valid price from {len(data['result'])} swap events")
        return None

    except requests.exceptions.RequestException as e:
        print(f"  Network error: {str(e)}")
        return None
    except Exception as e:
        print(f"  Unexpected error: {str(e)}")
        return None

def fetch_from_basescan(wallet: str, api_key: str, start_block: Optional[int] = None, end_block: Optional[int] = None) -> Optional[Dict]:
    """
    Fetch cbBTC-USDC LP transactions from Basescan API.

    NOTE: This is where we originally fetched the data for full_example_data.json
    The actual implementation that worked used transaction receipts and event logs.
    """
    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' library required for fetching from Basescan")
        print("Install with: pip install requests")
        print("Or use existing data files: full_example_data.json or sample_data.json")
        return None

    BASE_URL = "https://api.basescan.org/api"
    NFT_MANAGER = "0x827922686190790b37229fd06084350e74485b72"

    print(f"Fetching LP transactions for {wallet}...")
    print(f"Blocks: {start_block or 'earliest'} to {end_block or 'latest'}")

    params = {
        "module": "account",
        "action": "txlist",
        "address": wallet,
        "startblock": start_block if start_block else 0,
        "endblock": end_block if end_block else 99999999,
        "sort": "asc",
        "apikey": api_key
    }

    response = requests.get(BASE_URL, params=params)
    data = response.json()

    if data["status"] != "1":
        print(f"Error: {data.get('message', 'Failed to fetch transactions')}")
        return None

    all_txs = data["result"]
    lp_txs = [tx for tx in all_txs if tx.get("to", "").lower() == NFT_MANAGER.lower()]

    print(f"Found {len(lp_txs)} LP transactions")

    if not lp_txs:
        print("No LP transactions found for this wallet")
        return None

    actions = []

    for tx in lp_txs:
        tx_hash = tx["hash"]
        method_id = tx.get("input", "")[:10] if len(tx.get("input", "")) >= 10 else ""

        if method_id == "0x88316456":
            event = "Mint"
        elif method_id == "0x219f5d17":
            event = "IncreaseLiquidity"
        elif method_id == "0x0c49ccbe":
            event = "DecreaseLiquidity"
        elif method_id == "0xfc6f7865":
            event = "Collect"
        elif method_id == "0x42966c68":
            event = "Burn"
        else:
            continue

        print(f"Processing {event}: {tx_hash[:10]}...")

        time.sleep(0.2)

        receipt_params = {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
            "apikey": api_key
        }

        receipt_response = requests.get(BASE_URL, params=receipt_params)
        receipt_data = receipt_response.json()

        if "result" not in receipt_data or not receipt_data["result"]:
            continue

        receipt = receipt_data["result"]

        timestamp = datetime.fromtimestamp(int(tx["timeStamp"])).isoformat() + "+00:00"

        cbbtc = 0.0
        usdc = 0.0
        token_id = None

        MINT_SIG = "0x7a53080ba414158be7ec69b987b5fb7d07dee101bff85ac3f90d5c68ca679f40"
        BURN_SIG = "0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496"
        INCREASE_SIG = "0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f"
        DECREASE_SIG = "0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4"
        COLLECT_SIG = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"

        for log in receipt.get("logs", []):
            if not log.get("topics"):
                continue

            topic0 = log["topics"][0]

            if topic0 in [MINT_SIG, BURN_SIG, INCREASE_SIG, DECREASE_SIG, COLLECT_SIG]:
                if len(log["topics"]) > 1:
                    token_id = int(log["topics"][1], 16)

                data = log["data"][2:] if log["data"].startswith("0x") else log["data"]

                try:
                    if topic0 == COLLECT_SIG:
                        amount0_raw = int(data[64:128], 16)
                        amount1_raw = int(data[128:192], 16)
                    else:
                        amount0_raw = int(data[64:128], 16)
                        amount1_raw = int(data[128:192], 16)

                    usdc = amount0_raw / 10**6
                    cbbtc = amount1_raw / 10**8

                    break
                except (ValueError, IndexError):
                    continue

        if cbbtc > 0 and usdc > 0:
            block_num = int(tx.get('blockNumber', '0'))
            btc_price = get_pool_price_from_swap_logs(block_num, api_key)

            if not btc_price:
                print(f"\nERROR: Could not fetch pool price for block {block_num}")
                print(f"Transaction: {tx_hash}")
                print("Unable to calculate accurate cash flows without pool price data.")
                print("\nPossible reasons:")
                print("  1. No swap events near this block")
                print("  2. API rate limit or connectivity issue")
                print("  3. Pool was not yet deployed at this block")
                return None

            btc_price = float(btc_price)
            total_usd = cbbtc * btc_price + usdc

            if event in ["Mint", "IncreaseLiquidity"]:
                cash_flow = -total_usd
            else:
                cash_flow = total_usd
        else:
            continue

        action = {
            "timestamp": timestamp,
            "event": event,
            "token_id": token_id,
            "cbbtc": cbbtc,
            "usdc": usdc,
            "cash_flow": round(cash_flow, 2),
            "tx": tx_hash
        }

        actions.append(action)
        print(f"  ✓ cbBTC: {cbbtc:.8f}, USDC: {usdc:.2f}, Cash flow: ${cash_flow:.2f}")

    print(f"\nSuccessfully decoded {len(actions)} transactions")

    if not actions:
        print("\nNo valid LP transactions found or decoding failed.")
        print("Please check the wallet address and block range.")
        return None

    actual_start = min(int(tx["blockNumber"]) for tx in lp_txs)
    actual_end = max(int(tx["blockNumber"]) for tx in lp_txs)

    result = {
        "wallet": wallet,
        "start_block": actual_start,
        "end_block": actual_end,
        "actions": actions
    }

    return result

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Analyze Aerodrome LP performance with optional Basescan fetching',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --data-file full_example_data.json
  %(prog)s --data-file sample_data.json

  export BASESCAN_API_KEY=your_key_here
  %(prog)s --fetch 0xYourWallet
  %(prog)s --fetch 0xYourWallet --start-block 35102089 --end-block 35867540

  %(prog)s --format json --data-file full_example_data.json
  %(prog)s --format text --data-file full_example_data.json

Note: XIRR often fails for high-frequency rebalancing strategies.
      This tool provides TWR and APR as alternatives.
        '''
    )

    parser.add_argument(
        '--fetch',
        metavar='WALLET',
        help='Fetch data from Basescan for this wallet (requires API key)'
    )
    parser.add_argument(
        '--api-key',
        help='Basescan API key (or set BASESCAN_API_KEY env var)'
    )
    parser.add_argument(
        '--start-block',
        type=int,
        help='Start block for fetching (optional)'
    )
    parser.add_argument(
        '--end-block',
        type=int,
        help='End block for fetching (optional)'
    )

    parser.add_argument(
        '--data-file',
        help='Transaction data JSON file (default: full_example_data.json if exists)'
    )
    parser.add_argument(
        '--format',
        choices=['text', 'json', 'summary'],
        default='summary',
        help='Output format (default: summary)'
    )
    parser.add_argument(
        '--wallet',
        help='Override wallet address for display'
    )

    args = parser.parse_args()

    if args.fetch:
        api_key = args.api_key or os.environ.get('BASESCAN_API_KEY')
        if not api_key:
            print("ERROR: Basescan API key required for fetching")
            print("Either pass --api-key or set BASESCAN_API_KEY environment variable")
            print("Get free API key at: https://basescan.org/apis")
            return 1

        fetched_data = fetch_from_basescan(
            args.fetch,
            api_key,
            args.start_block,
            args.end_block
        )

        if fetched_data:
            output_file = f"lp_data_{args.fetch[:8]}.json"
            with open(output_file, 'w') as f:
                json.dump(fetched_data, f, indent=2)
            print(f"Data saved to: {output_file}")
            data_path = Path(output_file)
        else:
            print("\nFetch implementation incomplete. Use provided example files:")
            print("  python aerodrome_lp_analyzer.py --data-file full_example_data.json")
            return 1
    else:
        if not args.data_file:
            if Path('full_example_data.json').exists():
                args.data_file = 'full_example_data.json'
            else:
                args.data_file = 'xirr_from_receipts.json'

        data_path = Path(args.data_file)
        if not data_path.exists():
            print(f"Error: Data file '{args.data_file}' not found", file=sys.stderr)
            print(f"\nExpected JSON structure:", file=sys.stderr)
            print('''{
  "actions": [
    {
      "timestamp": "2025-09-04T...",
      "event": "IncreaseLiquidity" or "DecreaseLiquidity",
      "cbbtc": 0.00207616,
      "usdc": 1641.79,
      "cash_flow": -1840.15,
      "tx": "0x..."
    },
    ...
  ]
}''', file=sys.stderr)
            return 1

    try:
        analyzer = AerodromeAnalyzer(data_path, wallet_address=args.wallet)
        metrics = analyzer.analyze()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("\nTip: Ensure your data has non-zero 'cbbtc' values to calculate BTC prices.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error analyzing data: {e}", file=sys.stderr)
        return 1

    if args.format == 'json':
        output = {
            "wallet": metrics.wallet,
            "blocks": metrics.blocks,
            "initial_capital": float(metrics.initial_capital),
            "net_profit": float(metrics.net_profit),
            "twr_pct": float(metrics.twr),
            "apr_pct": float(metrics.apr),
            "apy_pct": float(metrics.apy),
            "divergence_loss": float(metrics.divergence_loss),
            "vs_hodl": float(metrics.vs_hodl),
            "hodl_apr_pct": float(metrics.hodl_apr),
            "vs_hodl_apr_pct": float(metrics.vs_hodl_apr),
            "rebalance_count": metrics.rebalance_count,
            "days_active": metrics.days_active,
            "btc_price_start": float(metrics.btc_price_start),
            "btc_price_end": float(metrics.btc_price_end),
            "xirr_pct": float(metrics.xirr) if metrics.xirr else None,
            "can_calculate_xirr": metrics.xirr is not None,
            "xirr_note": "XIRR converged successfully" if metrics.xirr else f"Failed to converge with {metrics.rebalance_count} rebalances"
        }
        print(json.dumps(output, indent=2))
        
    elif args.format == 'text':
        print("="*60)
        print("AERODROME LP PERFORMANCE ANALYSIS")
        print("="*60)
        print(f"Wallet: {metrics.wallet}")
        print(f"Blocks: {metrics.blocks}")
        print(f"\nBTC Price Movement:")
        print(f"  Start: ${metrics.btc_price_start:,.2f}")
        print(f"  End: ${metrics.btc_price_end:,.2f}")
        print(f"  Change: {((metrics.btc_price_end/metrics.btc_price_start - 1) * 100):.1f}%")
        print(f"\nCapital:")
        print(f"  Initial: ${metrics.initial_capital:,.2f}")
        print(f"  Final: ${metrics.final_capital:,.2f}")
        print(f"  Net Profit: ${metrics.net_profit:,.2f}")
        print(f"\nActivity:")
        print(f"  Days Active: {metrics.days_active}")
        print(f"  Rebalances: {metrics.rebalance_count}")
        if metrics.rebalance_count > 0:
            print(f"  Frequency: Every {metrics.days_active/metrics.rebalance_count:.1f} days")
        else:
            print(f"  Frequency: No rebalances")
        print(f"\nReturns:")
        if metrics.xirr:
            print(f"  XIRR: {metrics.xirr:.0f}%" if metrics.xirr > 10000 else f"  XIRR: {metrics.xirr:.2f}%")
        print(f"  TWR: {metrics.twr:.2f}%")
        print(f"  LP APR: {metrics.apr:.0f}%")
        print(f"  LP APY: {metrics.apy:.0f}%")
        print(f"  HODL APR: {metrics.hodl_apr:.0f}%")
        print(f"  Outperformance: {metrics.vs_hodl_apr:+.0f}% APR")
        print(f"\nRisk Metrics:")
        print(f"  Divergence Loss: ${metrics.divergence_loss:,.2f}")
        print(f"  vs HODL: ${metrics.vs_hodl:+,.2f}")
        
        if metrics.vs_hodl > 0:
            print(f"\n✅ LP outperformed HODL by ${metrics.vs_hodl:.2f}")
        else:
            print(f"\n❌ LP underperformed HODL by ${abs(metrics.vs_hodl):.2f}")
        
        print(f"\nXIRR Status:")
        if metrics.xirr:
            if metrics.xirr > 10000:
                print(f"  ⚠️ XIRR: {metrics.xirr:.0f}% (misleading due to sub-daily rebalancing)")
                print(f"  ℹ️ With {metrics.rebalance_count} rebalances in {metrics.days_active} days, XIRR compounds hourly")
                print(f"  ✅ Use APR ({metrics.apr:.0f}%) or TWR ({metrics.twr:.2f}%) instead")
            else:
                print(f"  ✅ XIRR calculated successfully: {metrics.xirr:.2f}%")
        else:
            print(f"  ⚠️ XIRR failed to converge with {metrics.rebalance_count} rebalances")
            print(f"  ✅ Use TWR ({metrics.twr:.2f}%) or APR ({metrics.apr:.0f}%) as alternatives")
        
    else:
        print(f"""
EXECUTIVE SUMMARY
================
Wallet: {metrics.wallet}
Blocks: {metrics.blocks}
Period: {metrics.days_active} days

Capital: ${metrics.initial_capital:,.0f} → +${metrics.net_profit:.0f} profit
Return: {metrics.net_profit/metrics.initial_capital*100:.0f}% ({metrics.apr:.0f}% APR)
Activity: {metrics.rebalance_count} rebalances
vs HODL: {'+$' + str(int(metrics.vs_hodl)) if metrics.vs_hodl > 0 else '-$' + str(int(abs(metrics.vs_hodl)))}

BTC moved: ${metrics.btc_price_start:,.0f} → ${metrics.btc_price_end:,.0f} (+{((metrics.btc_price_end/metrics.btc_price_start - 1) * 100):.0f}%)

Returns:
  {'XIRR: ' + str(int(metrics.xirr)) + '% ⚠️' if metrics.xirr and metrics.xirr > 10000 else 'XIRR: ' + str(int(metrics.xirr)) + '%' if metrics.xirr else 'XIRR: Failed to converge'}
  LP APR: {metrics.apr:.0f}%
  HODL APR: {metrics.hodl_apr:.0f}%
  Outperformance: {metrics.vs_hodl_apr:+.0f}% APR

Result: {'✅ Beat HODL by $' + str(int(metrics.vs_hodl)) if metrics.vs_hodl > 0 else '❌ Lost to HODL by $' + str(int(abs(metrics.vs_hodl)))}
""")

    return 0

if __name__ == "__main__":
    sys.exit(main())