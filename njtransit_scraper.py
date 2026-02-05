#!/usr/bin/env python3

import requests
import sqlite3
from datetime import datetime
import logging
import sys

# Configure logging for systemd journal (stdout)
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

class NJTransitScraper:
    def __init__(self, db_path='njtransit_data.db'):
        self.db_path = db_path
        # The modern raildata endpoint
        self.api_url = "https://raildata.njtransit.com/api/TrainData/getTrainSchedule19Rec"
        # NEED TO SET UP A TOKEN REFRESH SYSTEM IF IT EXPIRES
        self.api_token = "" #FIND TOKEN FROM BROWSER 
        self.setup_database()
        
    def setup_database(self):
        """Creates the table with a constraint to prevent duplicate trip rows."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS departures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_date DATE NOT NULL,
                train_number TEXT NOT NULL,
                line TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                destination TEXT NOT NULL,
                track TEXT,
                status TEXT,
                station_code TEXT,
                last_updated TIMESTAMP,
                -- Unique key: Prevents duplicates even if IDs are recycled in a day
                UNIQUE(service_date, train_number, scheduled_time, station_code)
            )
        ''')
        conn.commit()
        conn.close()
    
    def run_once(self, station_code='NY'):
        """Performs a single scrape and clean upsert."""
        # Payload must be multipart/form-data (handled by 'files' in requests)
        payload = {
            "token": (None, self.api_token),
            "station": (None, station_code)
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Origin': 'https://dv.njtransit.com',
            'Referer': 'https://dv.njtransit.com/'
        }
        
        try:
            logging.info(f"Checking {station_code} for NEC updates...")
            response = requests.post(self.api_url, files=payload, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if 'ITEMS' not in data:
                logging.warning("No data found in API response.")
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            now = datetime.now()
            service_date = now.strftime('%Y-%m-%d')
            saved_count = 0
            
            for train in data['ITEMS']:
                line = str(train.get('LINE', '')).upper()
                
                # Filter for Northeast Corridor variations
                if not any(term in line for term in ['NEC', 'NORTHEAST']):
                    continue

                train_num = str(train.get('TRAIN_ID', ''))
                track = str(train.get('TRACK', '')).strip()
                sched_time = str(train.get('SCHED_DEP_DATE', ''))
                dest = str(train.get('DESTINATION', ''))
                status = str(train.get('STATUS', ''))

                # UPSERT: Insert new trip OR update track/status of existing trip
                cursor.execute('''
                    INSERT INTO departures 
                    (service_date, train_number, line, scheduled_time, destination, track, status, station_code, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(service_date, train_number, scheduled_time, station_code) DO UPDATE SET
                        -- Only update track if the new data isn't empty (don't overwrite found data with TBD)
                        track = CASE WHEN excluded.track != '' THEN excluded.track ELSE departures.track END,
                        status = excluded.status,
                        last_updated = excluded.last_updated
                ''', (service_date, train_num, line, sched_time, dest, track, status, station_code, now))
                saved_count += 1
            
            conn.commit()
            conn.close()
            logging.info(f"Processed {saved_count} NEC records.")

        except Exception as e:
            logging.error(f"Scrape failed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    scraper = NJTransitScraper()
    scraper.run_once('NY')