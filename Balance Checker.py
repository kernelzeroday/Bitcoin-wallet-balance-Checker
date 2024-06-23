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
from asyncio import Queue

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler("debug.log"), logging.StreamHandler()])

def log_decorator(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        logging.debug(f"Calling function {func.__name__} with args: {args}, kwargs: {kwargs}")
        try:
            result = await func(*args, **kwargs)
            logging.debug(f"Function {func.__name__} returned: {result}")
            return result
        except Exception as e:
            logging.error(f"Error in function {func.__name__}: {e}")
            return None
    return wrapper

blacklist: Dict[str, float] = {}
pickle_file = 'addresses.pkl'
valid_accounts: Dict[str, float] = {}
api_failures: Dict[str, int] = {}
api_backoff: Dict[str, float] = {}

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
    if args.btc_rpc:
        return await get_balance_via_rpc(bitcoin_address)
    elif args.public_rpc:
        return await get_balance_via_public_rpc(bitcoin_address)
    else:
        return await get_balance_via_api(bitcoin_address)

@log_decorator
async def get_balance_via_api(bitcoin_address: str) -> float:
    apis = [
        f'https://blockchain.info/balance?active={bitcoin_address}',
        f'https://api.blockcypher.com/v1/btc/main/addrs/{bitcoin_address}/balance',
        f'https://sochain.com/api/v2/get_address_balance/BTC/{bitcoin_address}',
        f'https://api.blockchair.com/bitcoin/dashboards/address/{bitcoin_address}',
        f'https://chain.api.btc.com/v3/address/{bitcoin_address}',
        f'https://blockexplorer.com/api/addr/{bitcoin_address}/balance',
        f'https://blockstream.info/api/address/{bitcoin_address}',
        f'https://api.smartbit.com.au/v1/blockchain/address/{bitcoin_address}',
        f'https://btcbook.guarda.co/api/v2/address/{bitcoin_address}',
        f'https://api.coinbase.com/v2/accounts/{bitcoin_address}',
        f'https://api.blockcypher.com/v1/btc/main/addrs/{bitcoin_address}/balance',
        f'https://api.blockcypher.com/v1/btc/test3/addrs/{bitcoin_address}/balance',
        f'https://api.blockcypher.com/v1/btc/main/addrs/{bitcoin_address}/full',
        f'https://api.blockcypher.com/v1/btc/test3/addrs/{bitcoin_address}/full'
    ]
    random.shuffle(apis)
    async with aiohttp.ClientSession() as session:
        queue = Queue()
        for api_url in apis:
            await queue.put((api_url, bitcoin_address))
        tasks = [fetch_balance_from_queue(session, queue) for _ in range(4)]
        results = await asyncio.gather(*tasks)
        for result in results:
            if result is not None:
                return result

@log_decorator
async def fetch_balance_from_queue(session: aiohttp.ClientSession, queue: Queue) -> float:
    while not queue.empty():
        api_url, bitcoin_address = await queue.get()
        if api_url in api_backoff and time.time() < api_backoff[api_url]:
            logging.debug(f"Skipping {api_url} due to backoff")
            continue
        try:
            async with session.get(api_url) as response:
                logging.debug(f"Fetching balance for {bitcoin_address} from {api_url}: {response.status}")
                if response.status == 200:
                    data = await response.json()
                    logging.debug(f"Data received from {api_url}: {data}")
                    balance = extract_balance(data)
                    if balance is not None:
                        return balance
                else:
                    logging.debug(f"Error fetching balance for {bitcoin_address} from {api_url}: {response.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.debug(f"ClientError for {bitcoin_address} from {api_url}: {e}")
            api_failures[api_url] = api_failures.get(api_url, 0) + 1
            if api_failures[api_url] >= 3:
                backoff_time = min(60 * 2 ** (api_failures[api_url] - 3), 3600)
                api_backoff[api_url] = time.time() + backoff_time
                logging.debug(f"Backing off API {api_url} for {backoff_time} seconds due to repeated failures")
                continue
        finally:
            try:
                queue.task_done()
            except ValueError:
                logging.error(f"task_done() called too many times for {api_url}")
    return None

def extract_balance(data: dict) -> float:
    try:
        if 'final_balance' in data:
            return float(data['final_balance']) / 10**8
        elif 'balance' in data:
            return float(data['balance']) / 10**8
        elif 'data' in data and 'confirmed_balance' in data['data']:
            return float(data['data']['confirmed_balance'])
        elif 'amount' in data:
            return float(data['amount'])
        elif 'available_balance' in data:
            return float(data['available_balance'])
    except (TypeError, ValueError) as e:
        logging.error(f"Error extracting balance: {e}")
    return None

@log_decorator
async def get_balance_via_rpc(bitcoin_address: str) -> float:
    url = f"http://{args.rpc_user}:{args.rpc_password}@{args.rpc_host}:{args.rpc_port}"
    headers = {'content-type': 'application/json'}
    payload = json.dumps({"method": "getreceivedbyaddress", "params": [bitcoin_address], "jsonrpc": "2.0"})
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=payload) as response:
            logging.debug(f"Fetching balance for {bitcoin_address} via RPC: {response.status}")
            if response.status == 200:
                data = await response.json()
                logging.debug(f"Data received from RPC: {data}")
                return data['result']
            else:
                logging.debug(f"Error fetching balance for {bitcoin_address} via RPC: {response.status}")
    return None

@log_decorator
async def get_balance_via_public_rpc(bitcoin_address: str) -> float:
    public_rpcs = [
        {
            "url": "https://bitcoin-api.flare.network",
            "user": "public",
            "password": "d681co1pe2l3wcj9adrm2orlk0j5r5gr3wghgxt58tvge594co0k1ciljxq9glei"
        },
        {
            "url": "https://bitcoin.drpc.org",
            "user": "public",
            "password": "public"
        }
    ]
    headers = {'content-type': 'application/json'}
    payload = json.dumps({"method": "getreceivedbyaddress", "params": [bitcoin_address], "jsonrpc": "1.0", "id": "hc"})
    for rpc in public_rpcs:
        url = rpc["url"]
        auth = (rpc["user"], rpc["password"])
        try:
            response = requests.post(url, headers=headers, data=payload, auth=auth)
            logging.debug(f"Fetching balance for {bitcoin_address} via public RPC {url}: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                logging.debug(f"Data received from public RPC {url}: {data}")
                return data['result']
            else:
                logging.debug(f"Error fetching balance for {bitcoin_address} via public RPC {url}: {response.status_code}")
        except requests.RequestException as e:
            logging.debug(f"RequestException for {bitcoin_address} from {url}: {e}")
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
async def check_address_balance(address_queue: Queue, balance_queue: Queue, show_invalid: bool, recheck: bool, recheck_valids: bool):
    while True:
        generated_address, number, hex_private_key = await address_queue.get()
        if generated_address in valid_accounts and not recheck and not (recheck_valids and valid_accounts[generated_address] > 0):
            address_queue.task_done()
            continue
        balance = await get_balance(generated_address)
        if balance is not None and balance > 0:
            logging.info(colored(f"Decimal: {number}, Hex: {hex_private_key}, Address: {generated_address}, Balance: {balance} BTC", 'green'))
            valid_accounts[generated_address] = balance
            if args.use_pickle:
                save_pickle(valid_accounts, args.pickle_file)
            with open('valid_accounts.log', 'a') as f:
                f.write(f"Decimal: {number}, Hex: {hex_private_key}, Address: {generated_address}, Balance: {balance} BTC\n")
            await balance_queue.put((number, hex_private_key, generated_address, balance))
        elif show_invalid:
            logging.info(colored(f"Decimal: {number}, Hex: {hex_private_key}, Address: {generated_address}, Balance: {balance} BTC", 'red'))
        address_queue.task_done()

@log_decorator
async def generate_and_store_address(number: int, random_keys: bool, address_queue: Queue):
    if random_keys:
        private_key_bytes = random.randbytes(32)
        hex_private_key = private_key_bytes.hex()
    else:
        hex_private_key = hex(number)[2:].rjust(64, '0')
        private_key_bytes = bytes.fromhex(hex_private_key)
    generated_address = await generate_bitcoin_address(private_key_bytes)
    await address_queue.put((generated_address, number, hex_private_key))

@log_decorator
async def generate_addresses(start: int, end: int, random_keys: bool, thread_amount: int, address_queue: Queue, balance_queue: Queue, balance_queue_max: int):
    with ThreadPoolExecutor(max_workers=thread_amount) as executor:
        futures = []
        for number in range(start, end + 1):
            while balance_queue.qsize() >= balance_queue_max:
                await asyncio.sleep(1)
            futures.append(executor.submit(generate_and_store_address, number, random_keys, address_queue))
        for future in tqdm(as_completed(futures), total=len(futures), desc="Generating Addresses"):
            await future.result()

parser = argparse.ArgumentParser(description='Bitcoin Address Balance Checker')
parser.add_argument('--start', type=int, required=True, help='Start number for generating addresses')
parser.add_argument('--end', type=int, required=True, help='End number for generating addresses')
parser.add_argument('--random-keys', action='store_true', help='Generate random private keys instead of sequential')
parser.add_argument('--random-max-keys', type=int, help='Generate random private keys up to this number')
parser.add_argument('--show-invalid', action='store_true', help='Show addresses with zero balance')
parser.add_argument('--show-valid', action='store_true', help='Show addresses with balance from log file')
parser.add_argument('--thread-amount', type=int, default=10, help='Number of threads to use')
parser.add_argument('--recheck', action='store_true', help='Recheck all addresses')
parser.add_argument('--recheck-valids', action='store_true', help='Recheck only valid addresses')
parser.add_argument('--loop-forever', action='store_true', help='Loop forever generating and checking addresses')
parser.add_argument('--use-pickle', action='store_true', help='Use pickle file for storing valid accounts')
parser.add_argument('--pickle-file', type=str, default='addresses.pkl', help='Pickle file to use for storing valid accounts')
parser.add_argument('--btc-rpc', action='store_true', help='Use Bitcoin RPC instead of API calls')
parser.add_argument('--rpc-host', type=str, default='localhost', help='Bitcoin RPC host')
parser.add_argument('--rpc-port', type=int, default=8332, help='Bitcoin RPC port')
parser.add_argument('--rpc-user', type=str, help='Bitcoin RPC username')
parser.add_argument('--rpc-password', type=str, help='Bitcoin RPC password')
parser.add_argument('--public-rpc', action='store_true', help='Use public RPC instead of API calls')
parser.add_argument('--dump-pkl', action='store_true', help='Dump all info stored in the pickle file')
parser.add_argument('--balance-queue-max', type=int, default=1000, help='Maximum size of the balance queue')
args = parser.parse_args()

if args.use_pickle:
    valid_accounts = load_pickle(args.pickle_file)

async def main():
    global valid_accounts
    if args.show_valid:
        with open('valid_accounts.log', 'r') as f:
            for line in f:
                logging.info(colored(line.strip(), 'yellow'))
        return

    if args.dump_pkl:
        logging.info(colored(f"Dumping contents of pickle file {args.pickle_file}:", 'yellow'))
        for address, balance in valid_accounts.items():
            logging.info(colored(f"Address: {address}, Balance: {balance} BTC", 'yellow'))
        return

    address_queue = Queue()
    balance_queue = Queue()

    address_generator_task = asyncio.create_task(generate_addresses(args.start, args.end, args.random_keys, args.thread_amount, address_queue, balance_queue, args.balance_queue_max))
    balance_checker_task = asyncio.create_task(check_address_balance(address_queue, balance_queue, args.show_invalid, args.recheck, args.recheck_valids))

    await asyncio.gather(address_generator_task, balance_checker_task)

    while args.loop_forever:
        if args.random_max_keys:
            args.start = random.randint(0, args.random_max_keys)
            args.end = args.random_max_keys

        if args.start > args.end:
            logging.error("Invalid range. The start number should be less than or equal to the end number.")
        else:
            await generate_addresses(args.start, args.end, args.random_keys, args.thread_amount, address_queue, balance_queue, args.balance_queue_max)
            
            if args.use_pickle:
                valid_accounts = load_pickle(args.pickle_file)
            
            await check_address_balance(address_queue, balance_queue, args.show_invalid, args.recheck, args.recheck_valids)
            
            if args.use_pickle:
                save_pickle(valid_accounts, args.pickle_file)
        
        args.recheck = False
        args.recheck_valids = False

        # Save valid accounts to pickle file after each loop iteration
        if args.use_pickle:
            save_pickle(valid_accounts, args.pickle_file)

asyncio.run(main())
