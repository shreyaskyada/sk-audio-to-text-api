#!/usr/bin/env python3
"""
Test MongoDB connection to diagnose SSL/TLS issues
"""

import os
import sys
from dotenv import load_dotenv
from pymongo import MongoClient
import ssl

# Load environment variables
load_dotenv()

def test_ssl_support():
    """Test Python SSL support"""
    print("=" * 60)
    print("1. Testing Python SSL Support")
    print("=" * 60)
    
    try:
        print(f"✅ SSL Module Available: {ssl.OPENSSL_VERSION}")
        print(f"✅ SSL Version Info: {ssl.OPENSSL_VERSION_INFO}")
        
        # Check if TLS 1.2 is supported
        context = ssl.create_default_context()
        print(f"✅ Default SSL Context Created")
        print(f"   Protocol: {context.protocol}")
        print(f"   Check hostname: {context.check_hostname}")
        print(f"   Verify mode: {context.verify_mode}")
        
        return True
    except Exception as e:
        print(f"❌ SSL Error: {str(e)}")
        return False

def test_certifi():
    """Test certifi package"""
    print("\n" + "=" * 60)
    print("2. Testing Certifi (Python Certificates)")
    print("=" * 60)
    
    try:
        import certifi
        cert_path = certifi.where()
        print(f"✅ Certifi installed")
        print(f"   Certificate bundle: {cert_path}")
        
        # Check if cert file exists
        if os.path.exists(cert_path):
            print(f"✅ Certificate file exists")
            file_size = os.path.getsize(cert_path)
            print(f"   Size: {file_size} bytes")
        else:
            print(f"❌ Certificate file not found!")
            return False
            
        return True
    except ImportError:
        print("❌ Certifi not installed")
        print("   Fix: pip install --upgrade certifi")
        return False
    except Exception as e:
        print(f"❌ Certifi Error: {str(e)}")
        return False

def test_mongodb_drivers():
    """Test MongoDB driver versions"""
    print("\n" + "=" * 60)
    print("3. Testing MongoDB Drivers")
    print("=" * 60)
    
    try:
        import pymongo
        import motor
        
        print(f"✅ PyMongo version: {pymongo.__version__}")
        print(f"✅ Motor version: {motor.version}")
        
        # Check if versions are recent enough
        pymongo_version = tuple(map(int, pymongo.__version__.split('.')))
        if pymongo_version >= (4, 0, 0):
            print(f"✅ PyMongo version is recent")
        else:
            print(f"⚠️  PyMongo version is old, consider upgrading")
            
        return True
    except Exception as e:
        print(f"❌ Driver Error: {str(e)}")
        return False

def test_mongodb_connection():
    """Test actual MongoDB connection"""
    print("\n" + "=" * 60)
    print("4. Testing MongoDB Connection")
    print("=" * 60)
    
    mongodb_url = os.getenv("MONGODB_URL")
    
    if not mongodb_url:
        print("❌ MONGODB_URL not found in environment variables")
        print("   Create a .env file with MONGODB_URL=your_connection_string")
        return False
    
    # Mask password in URL for display
    display_url = mongodb_url
    if '@' in mongodb_url:
        parts = mongodb_url.split('@')
        if '://' in parts[0]:
            protocol_user = parts[0].split('://')
            if ':' in protocol_user[1]:
                username = protocol_user[1].split(':')[0]
                display_url = f"{protocol_user[0]}://{username}:****@{parts[1]}"
    
    print(f"Connection string: {display_url}")
    
    # Detect if it's MongoDB Atlas
    is_atlas = 'mongodb.net' in mongodb_url or 'mongodb+srv' in mongodb_url
    print(f"MongoDB Atlas: {'Yes' if is_atlas else 'No'}")
    
    # Test connection with different configurations
    configs = [
        {
            "name": "Default (with TLS)",
            "params": {
                'serverSelectionTimeoutMS': 5000,
                'tls': True,
                'tlsAllowInvalidCertificates': False,
            }
        },
        {
            "name": "With Invalid Certificates Allowed (Testing)",
            "params": {
                'serverSelectionTimeoutMS': 5000,
                'tls': True,
                'tlsAllowInvalidCertificates': True,
            }
        },
        {
            "name": "Without TLS (Local only)",
            "params": {
                'serverSelectionTimeoutMS': 5000,
            }
        }
    ]
    
    for config in configs:
        print(f"\n   Testing: {config['name']}")
        print(f"   " + "-" * 50)
        
        try:
            client = MongoClient(mongodb_url, **config['params'])
            
            # Try to ping
            result = client.admin.command('ping')
            
            if result.get('ok') == 1:
                print(f"   ✅ Connection successful!")
                
                # Get server info
                server_info = client.server_info()
                print(f"   MongoDB version: {server_info.get('version')}")
                
                # List databases
                dbs = client.list_database_names()
                print(f"   Accessible databases: {', '.join(dbs)}")
                
                client.close()
                return True
            else:
                print(f"   ❌ Ping failed: {result}")
                
        except Exception as e:
            error_msg = str(e)
            print(f"   ❌ Connection failed")
            
            # Provide specific error guidance
            if "SSL" in error_msg or "TLS" in error_msg:
                print(f"   Error type: SSL/TLS Error")
                print(f"   Suggestion: Update SSL certificates or Python SSL libraries")
            elif "authentication failed" in error_msg.lower():
                print(f"   Error type: Authentication Error")
                print(f"   Suggestion: Check username/password in connection string")
            elif "timeout" in error_msg.lower():
                print(f"   Error type: Timeout")
                print(f"   Suggestion: Check network/firewall or IP whitelist in MongoDB Atlas")
            else:
                print(f"   Error type: {error_msg[:100]}")
    
    return False

def print_recommendations():
    """Print recommendations based on test results"""
    print("\n" + "=" * 60)
    print("5. Recommendations")
    print("=" * 60)
    
    print("""
Quick Fixes (try in order):

1. Update Python certificates:
   pip install --upgrade certifi

2. Update MongoDB drivers:
   pip install --upgrade pymongo motor

3. Check MongoDB Atlas IP whitelist:
   - Go to MongoDB Atlas → Network Access
   - Add your current IP address
   - Wait 2-3 minutes

4. Verify connection string:
   - Should use mongodb+srv:// for Atlas
   - URL-encode special characters in password
   - Example: MyP@ss → MyP%40ss

5. Update OpenSSL (macOS):
   brew install openssl@3
   brew reinstall python@3.12

6. Use local MongoDB for development:
   brew install mongodb-community@7.0
   brew services start mongodb-community@7.0
   
   Then set in .env:
   MONGODB_URL=mongodb://localhost:27017

For more details, see: MONGODB_SSL_FIX.md
""")

def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("MongoDB Connection Diagnostic Tool")
    print("=" * 60 + "\n")
    
    results = {
        'ssl': test_ssl_support(),
        'certifi': test_certifi(),
        'drivers': test_mongodb_drivers(),
        'connection': test_mongodb_connection()
    }
    
    print_recommendations()
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name.upper():15} {status}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n✅ All tests passed! MongoDB connection is working.")
        return 0
    else:
        print("\n❌ Some tests failed. Review recommendations above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

