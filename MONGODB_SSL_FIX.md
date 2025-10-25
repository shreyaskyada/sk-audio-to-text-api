# MongoDB Atlas SSL/TLS Connection Error - Fix Guide

## ❌ Error

```
SSL handshake failed: [SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1010)
```

## 🔍 Root Causes

This error typically happens due to one of these reasons:

1. **Python SSL/TLS library issues** (most common)
2. **Outdated MongoDB drivers**
3. **Incorrect connection string**
4. **Network/firewall issues**
5. **MongoDB Atlas IP whitelist**

---

## ✅ Solution 1: Update Python SSL Libraries (Recommended)

### **For macOS (Your System):**

```bash
# Install/update OpenSSL via Homebrew
brew install openssl@3

# Reinstall Python with proper SSL support
brew reinstall python@3.12

# Or, if using pyenv:
CFLAGS="-I$(brew --prefix openssl)/include" \
LDFLAGS="-L$(brew --prefix openssl)/lib" \
pyenv install 3.12.10

# Reinstall certifi (Python certificates)
pip install --upgrade certifi
```

### **Verify SSL Support:**

```bash
python -c "import ssl; print(ssl.OPENSSL_VERSION)"
```

Should show: `OpenSSL 3.x.x` or higher

---

## ✅ Solution 2: Update MongoDB Drivers

```bash
pip install --upgrade pymongo motor
```

**Current versions in requirements.txt:**

- `motor==3.3.2`
- `pymongo==4.6.1`

**Try upgrading to latest:**

```bash
pip install motor==3.6.0 pymongo==4.10.1
```

---

## ✅ Solution 3: Fix MongoDB Connection String

### **Check Your Connection String Format:**

**Correct format for MongoDB Atlas:**

```
mongodb+srv://username:password@cluster.mongodb.net/database?retryWrites=true&w=majority
```

**Common issues:**

- ❌ Special characters in password not URL-encoded
- ❌ Missing `+srv` in connection string
- ❌ Wrong cluster hostname

### **URL Encode Special Characters:**

If your password has special characters like `@`, `#`, `$`, etc., encode them:

| Character | Encoded |
| --------- | ------- |
| `@`       | `%40`   |
| `#`       | `%23`   |
| `$`       | `%24`   |
| `%`       | `%25`   |
| `&`       | `%26`   |
| `+`       | `%2B`   |

**Example:**

```
Password: MyP@ss#123
Encoded:  MyP%40ss%23123
```

---

## ✅ Solution 4: Whitelist Your IP in MongoDB Atlas

1. Go to MongoDB Atlas Dashboard
2. Click **Network Access** (left sidebar)
3. Click **Add IP Address**
4. Options:
   - **Add Current IP Address** (for local development)
   - **Allow Access from Anywhere** (`0.0.0.0/0`) - for testing only!
5. Click **Confirm**

**Wait 2-3 minutes** for changes to propagate.

---

## ✅ Solution 5: Use Temporary Workaround (Testing Only)

**⚠️ NOT for production!**

Update `app/mongodb.py` to allow invalid certificates temporarily:

```python
connection_params.update({
    'tls': True,
    'tlsAllowInvalidCertificates': True,  # ⚠️ Testing only!
    'retryWrites': True,
    'w': 'majority'
})
```

This helps identify if it's an SSL certificate issue.

---

## ✅ Solution 6: Install Python SSL Certificates (macOS)

macOS sometimes has certificate issues. Run this:

```bash
# For system Python
/Applications/Python\ 3.12/Install\ Certificates.command

# Or manually install certificates
pip install --upgrade certifi
python -c "import certifi; print(certifi.where())"
```

---

## ✅ Solution 7: Use Local MongoDB for Development

If MongoDB Atlas continues to have issues, use local MongoDB:

### **Install MongoDB locally:**

```bash
# macOS
brew tap mongodb/brew
brew install mongodb-community@7.0
brew services start mongodb-community@7.0
```

### **Update .env:**

```env
MONGODB_URL=mongodb://localhost:27017
MONGODB_DATABASE=audio_transcription
```

---

## 🔧 Updated Code (Already Applied)

I've updated `app/mongodb.py` with better SSL/TLS handling:

```python
connection_params = {
    'serverSelectionTimeoutMS': 5000,
    'connectTimeoutMS': 10000,
    'socketTimeoutMS': 20000,
}

if 'mongodb.net' in mongodb_url or 'mongodb+srv' in mongodb_url:
    connection_params.update({
        'tls': True,
        'tlsAllowInvalidCertificates': False,
        'retryWrites': True,
        'w': 'majority'
    })
```

---

## 🧪 Test Connection

### **Method 1: Python Script**

```python
from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

mongodb_url = os.getenv("MONGODB_URL")
print(f"Testing connection to: {mongodb_url[:30]}...")

try:
    client = MongoClient(
        mongodb_url,
        serverSelectionTimeoutMS=5000,
        tls=True,
        tlsAllowInvalidCertificates=False
    )
    client.admin.command('ping')
    print("✅ MongoDB connection successful!")
    client.close()
except Exception as e:
    print(f"❌ Connection failed: {str(e)}")
```

### **Method 2: MongoDB Compass**

1. Download [MongoDB Compass](https://www.mongodb.com/products/compass)
2. Paste your connection string
3. Click **Connect**
4. If it works, the issue is in your Python environment

---

## 🎯 Recommended Fix Order

Try these in order:

1. ✅ **Check IP whitelist** in MongoDB Atlas (5 minutes)
2. ✅ **Update SSL certificates**: `pip install --upgrade certifi` (2 minutes)
3. ✅ **Update MongoDB drivers**: `pip install --upgrade pymongo motor` (2 minutes)
4. ✅ **Verify connection string** format (check for special characters)
5. ✅ **Test with MongoDB Compass** (verify Atlas is accessible)
6. ✅ **Upgrade OpenSSL** via Homebrew (10 minutes)
7. ✅ **Use local MongoDB** for development (if all else fails)

---

## 📝 Quick Checklist

- [ ] IP address whitelisted in MongoDB Atlas
- [ ] Connection string uses `mongodb+srv://`
- [ ] Password special characters are URL-encoded
- [ ] Python SSL version is 1.1.1 or higher
- [ ] `pymongo` and `motor` are latest versions
- [ ] `certifi` package is up to date
- [ ] Can connect via MongoDB Compass
- [ ] Network allows outbound connections to port 27017

---

## 🚀 Alternative: Make MongoDB Optional

Update `app/main.py` startup to continue without MongoDB:

```python
@app.on_event("startup")
async def startup_event():
    # ... other startup code ...

    # Connect to MongoDB (optional for development)
    try:
        await connect_to_mongo()
        logger.info("MongoDB connection established successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {str(e)}")
        logger.warning("Continuing without MongoDB - audit logs will be file-only")
        # Don't raise exception - allow app to run without MongoDB
```

This way your app works even if MongoDB is down!

---

## 📞 Still Not Working?

### **Get More Details:**

```bash
# Check OpenSSL version
python -c "import ssl; print(ssl.OPENSSL_VERSION)"

# Check certifi location
python -c "import certifi; print(certifi.where())"

# Test basic SSL
python -c "import ssl; import socket; print(ssl.create_default_context())"
```

### **Enable Debug Logging:**

Add to `app/mongodb.py`:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

**Most Common Fix:** Whitelist your IP in MongoDB Atlas + Update certifi

```bash
pip install --upgrade certifi pymongo motor
```

Then restart your application!
