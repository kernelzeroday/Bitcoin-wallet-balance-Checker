import ecdsa
import hashlib
import base58
import requests

def get_balance(bitcoin_address):
    api_url = f'https://blockchain.info/balance?active={bitcoin_address}'
    response = requests.get(api_url)
    
    if response.status_code == 200:
        data = response.json()
        return data.get('final_balance', 0) / 10**8  # Convert satoshis to BTC
    else:
        print(f"Error fetching balance for {bitcoin_address}: {response.status_code}")
        return 0

def generate_bitcoin_address(private_key_bytes):
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

def print_addresses_with_balance(start, end):
    for number in range(start, end + 1):
        hex_private_key = hex(number)[2:].rjust(64, '0')
        private_key_bytes = bytes.fromhex(hex_private_key)
        generated_address = generate_bitcoin_address(private_key_bytes)
        balance = get_balance(generated_address)

        print(f"Decimal: {number}, Hex: {hex_private_key}, Address: {generated_address}, Balance: {balance} BTC")

# Get user input for the range
start = int(input("Enter the start number: "))
end = int(input("Enter the end number: "))

# Validate the range
if start > end:
    print("Invalid range. The start number should be less than or equal to the end number.")
else:
    # Print addresses with balance within the specified range
    print_addresses_with_balance(start, end)
