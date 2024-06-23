import ecdsa
import hashlib
import base58
import requests
import argparse
import random
import json
import logging
from termcolor import colored
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time
from typing import List, Dict, Tuple
from functools import wraps
import aiohttp
import asyncio
import pickle
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler("debug.log"), logging.StreamHandler()])

def log_decorator(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        logging.debug(f"Calling function {func.__name__} with args: {args}, kwargs: {kwargs}")
        result = await func(*args, **kwargs)
        logging.debug(f"Function {func.__name__} returned: {result}")
        return result
    return wrapper

blacklist: Dict[str, float] = {}
pickle_file = 'addresses.pkl'
valid_accounts: Dict[str, float] = {}
generated_addresses: List[Tuple[int, str, str]] = []

def load_pickle(file: str) -> Dict[str, float]:
    if os.path.exists(file):
        with open(file, 'rb') as f:
            return pickle.load(f)
    return {}

def save_pickle(data: Dict[str, float], file: str):
    with open(file, 'wb') as f:
        pickle.dump(data, f)
@log_decorator
async def get_balance(bitcoin_address: str) -> float:
    apis = [
        f'https://blockchain.info/balance?active={bitcoin_address}',
        f'https://api.blockcypher.com/v1/btc/main/addrs/{bitcoin_address}/balance',
        f'https://sochain.com/api/v2/get_address_balance/BTC/{bitcoin_address}',
        f'https://chain.api.btc.com/v3/address/{bitcoin_address}',
        f'https://blockexplorer.com/api/addr/{bitcoin_address}/balance',
        f'https://blockstream.info/api/address/{bitcoin_address}'
    ]
    async with aiohttp.ClientSession() as session:
        while True:
            random.shuffle(apis)
            tasks = [fetch_balance(session, api_url, bitcoin_address) for api_url in apis]
            results = await asyncio.gather(*tasks)
            for result in results:
                if result is not None:
                    return result
            await asyncio.sleep(1)

@log_decorator
async def fetch_balance(session: aiohttp.ClientSession, api_url: str, bitcoin_address: str) -> float:
    try:
        async with session.get(api_url) as response:
            if response.status == 200:
                data = await response.json()
                if 'final_balance' in data:
                    return data['final_balance'] / 10**8
                elif 'balance' in data:
                    return data['balance'] / 10**8
                elif 'data' in data and 'confirmed_balance' in data['data']:
                    return float(data['data']['confirmed_balance'])
            else:
                logging.debug(f"Error fetching balance for {bitcoin_address} from {api_url}: {response.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logging.debug(f"ClientError for {bitcoin_address} from {api_url}: {e}")
    return None
@log_decorator
async def generate_bitcoin_address(private_key_bytes: bytes) -> str:
    sk = ecdsa.SigningKey.from_string(private_key_bytes, curve=ecdsa.SECP256k1)
    vk = sk.verifying_key
    compressed_public_key = vk.to_string("compressed")
    sha256_hash = hashlib.sha256(compressed_public_key).digest()
    ripemd160_hash = hashlib.new('ripemd160', sha256_hash).digest()
    extended_ripemd160_hash = b'\x00' + ripemd160_hash
    checksum = hashlib.sha256(hashlib.sha256(extended_ripemd160_hash).digest()).digest()[:4]
    extended_hash_with_checksum = extended_ripemd160_hash + checksum
    bitcoin_address = base58.b58encode(extended_hash_with_checksum).decode('utf-8')
    return bitcoin_address

@log_decorator
async def check_address_balance(generated_address: str, number: int, hex_private_key: str, show_invalid: bool, recheck: bool, recheck_valids: bool):
    if generated_address in valid_accounts and not recheck and not (recheck_valids and valid_accounts[generated_address] > 0):
        return None
    balance = await get_balance(generated_address)
    if balance > 0:
        logging.info(colored(f"Decimal: {number}, Hex: {hex_private_key}, Address: {generated_address}, Balance: {balance} BTC", 'green'))
        valid_accounts[generated_address] = balance
        if args.use_pickle:
            save_pickle(valid_accounts, args.pickle_file)
        return (number, hex_private_key, generated_address, balance)
    elif show_invalid:
        logging.info(colored(f"Decimal: {number}, Hex: {hex_private_key}, Address: {generated_address}, Balance: {balance} BTC", 'red'))
    return None

@log_decorator
async def generate_and_store_address(number: int, random_keys: bool):
    if random_keys:
        private_key_bytes = random.randbytes(32)
        hex_private_key = private_key_bytes.hex()
    else:
        hex_private_key = hex(number)[2:].rjust(64, '0')
        private_key_bytes = bytes.fromhex(hex_private_key)
    generated_address = await generate_bitcoin_address(private_key_bytes)
    generated_addresses.append((number, hex_private_key, generated_address))

@log_decorator
async def check_all_generated_addresses(show_invalid: bool, recheck: bool, recheck_valids: bool):
    total_found = 0
    accounts_with_balance: List[Tuple[int, str, str, float]] = []
    tasks = [check_address_balance(addr[2], addr[0], addr[1], show_invalid, recheck, recheck_valids) for addr in generated_addresses]
    for task in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Checking Balances"):
        result = await task
        if result:
            total_found += 1
            accounts_with_balance.append(result)
    logging.info(colored(f"Total accounts with balance: {total_found}", 'blue'))
    for account in accounts_with_balance:
        logging.info(colored(f"Decimal: {account[0]}, Hex: {account[1]}, Address: {account[2]}, Balance: {account[3]} BTC", 'yellow'))

@log_decorator
async def generate_addresses(start: int, end: int, random_keys: bool, thread_amount: int):
    with ThreadPoolExecutor(max_workers=thread_amount) as executor:
        futures = [executor.submit(generate_and_store_address, number, random_keys) for number in range(start, end + 1)]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Generating Addresses"):
            await future.result()

parser = argparse.ArgumentParser(description='Bitcoin Address Balance Checker')
parser.add_argument('--start', type=int, required=True, help='Start number for generating addresses')
parser.add_argument('--end', type=int, required=True, help='End number for generating addresses')
parser.add_argument('--random-keys', action='store_true', help='Generate random private keys instead of sequential')
parser.add_argument('--random-max-keys', type=int, help='Generate random private keys up to this number')
parser.add_argument('--show-invalid', action='store_true', help='Show addresses with zero balance')
parser.add_argument('--thread-amount', type=int, default=10, help='Number of threads to use')
parser.add_argument('--recheck', action='store_true', help='Recheck all addresses')
parser.add_argument('--recheck-valids', action='store_true', help='Recheck only valid addresses')
parser.add_argument('--loop-forever', action='store_true', help='Loop forever generating and checking addresses')
parser.add_argument('--use-pickle', action='store_true', help='Use pickle file for storing valid accounts')
parser.add_argument('--pickle-file', type=str, default='addresses.pkl', help='Pickle file to use for storing valid accounts')
args = parser.parse_args()

if args.use_pickle:
    valid_accounts = load_pickle(args.pickle_file)

async def main():
    while True:
        if args.random_max_keys:
            args.start = random.randint(0, args.random_max_keys)
            args.end = args.random_max_keys

        if args.start > args.end:
            logging.error("Invalid range. The start number should be less than or equal to the end number.")
        else:
            await generate_addresses(args.start, args.end, args.random_keys, args.thread_amount)
            await check_all_generated_addresses(args.show_invalid, args.recheck, args.recheck_valids)
        
        if not args.loop_forever:
            break

asyncio.run(main())
