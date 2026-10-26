#!/bin/bash

echo "🚑 Starting Eir EMT System (Python/FastAPI Backend)..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo "Please create a .env file with your API keys"
    echo ""
    echo "Required environment variables:"
    echo "  JWT_SECRET=your-super-secret-jwt-key-here"
    echo "  OPENAI_API_KEY=your-openai-api-key-here"
    exit 1
fi

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed!"
    echo "Please install Python 3.8 or higher"
    exit 1
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
python3 -m pip install -r requirements.txt -q

# Create uploads directory
echo "📁 Creating uploads directory..."
mkdir -p uploads

echo "✅ Setup complete!"
echo ""
echo "🚀 Starting Python backend on port 5000..."
echo ""

# Start the FastAPI server
python3 main.py
