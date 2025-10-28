# MongoDB Integration for Feedback Storage

## Overview

The refactored application uses **MongoDB** for storing user feedback about transcription quality. This provides a scalable, persistent storage solution for feedback data.

## Why MongoDB?

- ✅ **Persistent Storage** - Data survives application restarts
- ✅ **Scalable** - Can handle large amounts of feedback
- ✅ **Flexible Schema** - Easy to add new fields
- ✅ **Production Ready** - Suitable for deployment
- ✅ **Query Power** - Advanced aggregation and filtering

## Quick Setup

### Option 1: Docker (Recommended)

Easiest way to get MongoDB running:

```bash
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

That's it! MongoDB is now running on `localhost:27017`.

### Option 2: Local Installation

**macOS:**

```bash
brew tap mongodb/brew
brew install mongodb-community
brew services start mongodb-community
```

**Ubuntu/Debian:**

```bash
wget -qO - https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu focal/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
sudo apt-get update
sudo apt-get install -y mongodb-org
sudo systemctl start mongod
```

**Windows:**

- Download from [MongoDB Download Center](https://www.mongodb.com/try/download/community)
- Run installer
- MongoDB will run as a Windows service

## Configuration

Add these to your `.env` file:

```bash
# MongoDB Configuration
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=audio-to-text-db
```

### Remote MongoDB (MongoDB Atlas, etc.)

If using a remote MongoDB instance:

```bash
MONGODB_URL=mongodb+srv://username:password@cluster.mongodb.net/
MONGODB_DB_NAME=audio-to-text-db
```

## Database Structure

### Collection: `feedback`

Each feedback document contains:

```json
{
  "_id": ObjectId("..."),
  "rating": 5,
  "rating_text": "Excellent transcription",
  "feedback": "Medical terminology was accurate",
  "transcription_preview": "The patient is a 31 year old...",
  "errors_found": [
    {"wrong": "catching lock", "correct": "catching, locking"}
  ],
  "total_errors": 1,
  "feedback_type": "detailed",
  "timestamp": ISODate("2025-10-27T17:47:45.821Z")
}
```

## API Endpoints

### Submit Feedback

```bash
POST /api/v1/feedback/submit
```

### Get Statistics

```bash
GET /api/v1/feedback/stats
```

### Get All Feedback

```bash
GET /api/v1/feedback/all
```

## Testing Connection

The application will automatically test the MongoDB connection on startup. You'll see:

```
✅ Connected to MongoDB: audio-to-text-db
```

If MongoDB is not available, you'll see:

```
❌ MongoDB connection failed: ...
⚠️  Continuing without MongoDB - feedback storage will be limited
```

## Viewing Data

### Using MongoDB Compass (GUI)

1. Download [MongoDB Compass](https://www.mongodb.com/products/compass)
2. Connect to `mongodb://localhost:27017`
3. Browse the `audio-to-text-db` database
4. View the `feedback` collection

### Using MongoDB Shell

```bash
# Connect to MongoDB
mongosh

# Switch to database
use audio-to-text-db

# View all feedback
db.feedback.find().pretty()

# Count feedback entries
db.feedback.countDocuments()

# Get average rating
db.feedback.aggregate([
  { $match: { rating: { $gt: 0 } } },
  { $group: { _id: null, avgRating: { $avg: "$rating" } } }
])
```

## Backup & Export

### Export Feedback Data

```bash
mongoexport --db=audio-to-text-db --collection=feedback --out=feedback_backup.json
```

### Import Feedback Data

```bash
mongoimport --db=audio-to-text-db --collection=feedback --file=feedback_backup.json
```

## Troubleshooting

### Connection Failed

**Problem:** `MongoDB connection failed`

**Solutions:**

1. Check if MongoDB is running: `brew services list` (macOS) or `sudo systemctl status mongod` (Linux)
2. Verify connection URL in `.env`
3. Check firewall settings
4. Ensure MongoDB port (27017) is not blocked

### Permission Errors

**Problem:** `MongoServerError: not authorized`

**Solution:** Update your connection string with credentials:

```bash
MONGODB_URL=mongodb://username:password@localhost:27017
```

### Port Already in Use

**Problem:** `Address already in use`

**Solution:** Another process is using port 27017. Either:

- Stop the other process
- Use a different port: `MONGODB_URL=mongodb://localhost:27018`

## Production Deployment

### Recommended Services

1. **MongoDB Atlas** (Recommended)

   - Free tier available
   - Managed service
   - Automatic backups
   - Global deployment
   - URL: https://www.mongodb.com/cloud/atlas

2. **DigitalOcean Managed MongoDB**

   - Starting at $15/month
   - Fully managed
   - Automatic backups

3. **AWS DocumentDB**
   - MongoDB-compatible
   - Integrated with AWS
   - Enterprise features

### Security Best Practices

1. **Use Authentication:**

   ```bash
   MONGODB_URL=mongodb://username:password@host:27017/
   ```

2. **Enable SSL/TLS:**

   ```bash
   MONGODB_URL=mongodb+srv://...
   ```

3. **IP Whitelisting:**

   - Only allow connections from your application servers

4. **Regular Backups:**

   - Set up automated daily backups
   - Test restore procedures

5. **Monitor Performance:**
   - Set up alerts for connection failures
   - Monitor query performance

## Migration from JSON Storage

If you were previously using JSON file storage, you can migrate your data:

```python
import json
from pymongo import MongoClient

# Load old JSON data
with open('feedback_data.json', 'r') as f:
    feedback_list = json.load(f)

# Connect to MongoDB
client = MongoClient('mongodb://localhost:27017')
db = client['audio-to-text-db']

# Insert data
db.feedback.insert_many(feedback_list)

print(f"Migrated {len(feedback_list)} feedback entries")
```

## Summary

MongoDB provides a robust, production-ready storage solution for the feedback system:

- ✅ **Easy Setup** - Docker or local installation
- ✅ **Reliable** - Persistent and durable
- ✅ **Scalable** - Grows with your application
- ✅ **Feature Rich** - Advanced queries and aggregations
- ✅ **Production Ready** - Battle-tested in production

For any issues or questions, refer to the [MongoDB Documentation](https://docs.mongodb.com/).
