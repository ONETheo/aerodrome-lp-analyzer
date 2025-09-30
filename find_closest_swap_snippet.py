#!/usr/bin/env python3
"""
Snippet: Find closest Swap event AT or AFTER a target block
For Aerodrome Slipstream cbBTC-USDC pool on Base
"""
import os
import requests
import time
from decimal import Decimal, getcontext

getcontext().prec = 50

# Configuration
POOL_ADDRESS = "0x4e962BB3889Bf030368F56810A9c96B83CB3E778"  # cbBTC-USDC Slipstream pool
SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
API_KEY = os.getenv('ETHERSCAN_API_KEY', 'YOUR_API_KEY_HERE')  # Get your key from basescan.org
BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = 8453  # Base

CBBTC_DECIMALS = 8
USDC_DECIMALS = 6

def fetch_swaps_near_block(target_block, search_range=200):
    """
    Fetch Swap events AT or AFTER the target block

    Args:
        target_block: Block number of your LP event
        search_range: How many blocks ahead to search

    Returns:
        List of swap log events
    """
    from_block = target_block  # Start AT the target block
    to_block = target_block + search_range  # Look ahead

    params = {
        'chainid': CHAIN_ID,
        'module': 'logs',
        'action': 'getLogs',
        'address': POOL_ADDRESS,
        'topic0': SWAP_TOPIC,
        'fromBlock': from_block,
        'toBlock': to_block,
        'apikey': API_KEY
    }

    retries = 3
    for attempt in range(retries):
        try:
            response = requests.get(BASE_URL, params=params, timeout=30)
            data = response.json()

            if data['status'] == '1':
                return data.get('result', [])
            elif 'rate limit' in data.get('message', '').lower():
                time.sleep((attempt + 1) * 2)
            else:
                print(f"API Error: {data.get('message', 'Unknown error')}")
                return []
        except Exception as e:
            print(f"Error: {e}")
            if attempt < retries - 1:
                time.sleep(2)

    return []

def decode_swap_price(log):
    """
    Decode cbBTC price from Swap event

    Swap event data structure:
    - data[0:64]:     amount0 (64 hex chars)
    - data[64:128]:   amount1
    - data[128:192]:  sqrtPriceX96 (this is what we need!)
    - data[192:256]:  liquidity
    - data[256:320]:  tick

    Returns:
        Dict with block, tx_hash, tick, and cbbtc_price
    """
    block = int(log['blockNumber'], 16)
    tx_hash = log['transactionHash']

    # Extract sqrtPriceX96 from data field
    data = log['data'][2:]  # Remove '0x' prefix
    sqrtPriceX96_hex = data[128:192]  # Bytes 64-95 (hex chars 128-191)
    tick_hex = data[256:320]  # Bytes 128-159 (hex chars 256-319)

    # Convert tick (signed integer)
    def hex_to_signed_int(hex_str):
        val = int(hex_str, 16)
        if val >= 2**255:  # If high bit set, it's negative
            val -= 2**256
        return val

    sqrtPriceX96 = int(sqrtPriceX96_hex, 16)
    tick = hex_to_signed_int(tick_hex)

    # Calculate price from sqrtPriceX96
    # Formula: price = (sqrtPriceX96 / 2^96)^2
    Q96 = Decimal(2**96)
    sqrt_price = Decimal(sqrtPriceX96) / Q96
    price_ratio = sqrt_price ** 2  # This is USDC per cbBTC

    # Convert to cbBTC price in USDC
    # Adjust for decimal differences (cbBTC=8, USDC=6)
    decimal_adjustment = Decimal(10 ** (CBBTC_DECIMALS - USDC_DECIMALS))
    cbbtc_price = (Decimal(1) / price_ratio) * decimal_adjustment

    return {
        'block': block,
        'tx_hash': tx_hash,
        'tick': tick,
        'cbbtc_price': cbbtc_price
    }

def find_closest_swap(target_block, search_range=200):
    """
    Find the closest Swap AT or immediately AFTER target block

    Args:
        target_block: Block number of your LP event
        search_range: How many blocks ahead to search

    Returns:
        Dict with swap details or None if not found
    """
    # Fetch swap logs
    swap_logs = fetch_swaps_near_block(target_block, search_range)

    if not swap_logs:
        print(f"No swaps found near block {target_block}")
        return None

    # Decode all swaps
    swaps = []
    for log in swap_logs:
        try:
            swap = decode_swap_price(log)
            swaps.append(swap)
        except Exception as e:
            print(f"Error decoding swap: {e}")
            continue

    # Find swap AT the exact block
    at_block_swaps = [s for s in swaps if s['block'] == target_block]
    if at_block_swaps:
        swap = at_block_swaps[0]  # Use first swap at this block
        print(f"Found swap AT block {target_block}")
        return swap

    # Find first swap AFTER the block
    after_block_swaps = [s for s in swaps if s['block'] > target_block]
    if after_block_swaps:
        swap = min(after_block_swaps, key=lambda s: s['block'])  # Closest one
        blocks_away = swap['block'] - target_block
        print(f"Found swap {blocks_away} blocks after (block {swap['block']})")
        return swap

    print(f"No swap found at or after block {target_block}")
    return None

# Example usage
if __name__ == "__main__":
    # Example: Find price for block 35867531 (your last LP event)
    TARGET_BLOCK = 35867531

    print(f"\nSearching for closest Swap to block {TARGET_BLOCK}...")
    print("="*80)

    swap = find_closest_swap(TARGET_BLOCK)

    if swap:
        print(f"\n✓ Found Swap:")
        print(f"  Block: {swap['block']}")
        print(f"  TX: {swap['tx_hash']}")
        print(f"  cbBTC Price: ${swap['cbbtc_price']:,.2f}")
        print(f"  Tick: {swap['tick']}")
    else:
        print("\n✗ No swap found")

    print("="*80)
