import logging
import os
import json
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from todoist_api_python.api import TodoistAPI
from datetime import datetime, timedelta, timezone
from dateutil import parser # Import robust ISO 8601 parser
import pytz
import re
import requests
import time
#import traceback #uncomment me if error messages in this code are giving you close to nothing to go on to solve it. 

# --- Setup ---
# Logging
logging.basicConfig(
    filename="task_scheduler.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

def log_message(level, message):
    """Set up of logging functionality - logging messages are captured in task_scheduler.log"""
    if level == "INFO":
        logging.info(message)
    elif level == "ERROR":
        logging.error(message)
    elif level == "WARNING":
        logging.warning(message)
    print(message)  # Immediate feedback

# Set timezone dynamically
local_tz = pytz.timezone('Europe/London') #Automatically handles transitions between GMT/BST 

# Load API keys - .env added to gitignore so these will not accidentally be uploaded to GitHub. 
load_dotenv(dotenv_path="API_keys.env")

def refresh_token_if_needed():
    """ Checks if the token for Google services is expired and refreshes it if needed.
    Parameter(s):
        none
    Returns:
        creds: credentials for Google services as token.json
    """
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/calendar']
    creds = None
    # Token file is generated after initial authentication
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # If there are no credentials available or the token is expired
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())  # Refresh the token if expired

        # If there is no valid token or refresh fails, force reauthorization
        else:
            print("No valid credentials available, please reauthorize.")
            return None

        # Save the refreshed credentials for next time
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return creds

# Authenticate Google API using OAuth2.0
def authenticate_google_services():
    """ Authenticates credentials for using Google services.
    Parameter(s):
        none
    Returns:
        sheets_service:  Google Sheets service instance
        calendar_service: Google Calendar service instance
    """
    # Define the required scopes for Sheets and Calendar
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/calendar']

    # Check if token.json file exists to load existing credentials
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If no valid credentials are available, prompt the user to log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json',
                SCOPES
            )
            creds = flow.run_local_server(port=8081)
        
        # Save the credentials for future use
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    # Build the services using the credentials
    sheets_service = build('sheets', 'v4', credentials=creds)
    calendar_service = build('calendar', 'v3', credentials=creds)
    
    return sheets_service, calendar_service

# -- Get weather from Met Office DataHub API ---
# Pull api_key from .env file
api_key = os.getenv("API_KEY")
if not api_key:
    raise ValueError("API key is not set. Please check your environment variables.")

#Define location for where to call the API for, as latitude and longitude - currently set Grimsby, North East Lincolnshire. 
latitude = '53.56685606194195' # To change, pull lat. and long. from Google Maps
longitude = '-0.08339315195789283'
base_url = 'https://data.hub.api.metoffice.gov.uk/sitespecific/v0' # Define the base URL for the Met Office DataHub API
endpoint = '/point/hourly' # Specify the endpoint for the type of weather data you need (e.g., hourly forecast)

# Build the full URL includeLocationName=TRUE means json response also contains the name of the weather station it is pulling data from. 
# If recently changed, also print full response in def get_weather to confirm identity of weather station
url = f"{base_url}{endpoint}?includeLocationName=TRUE&latitude={latitude}&longitude={longitude}"

# Set up headers for the request
headers = {
    'Accept': 'application/json',
    'apikey': api_key,
    'User-Agent': 'Python/requests'
}
# Create Cache file for weather data, so that we don't continue to call the API every time the code reruns due to new meeting/task etc.
weather_cache = 'weather_cache.json'

def get_weather(): 
    """Retrieves the next 48 hours of data for the chosen weather station as JSON, keeps only time and feels-like temperature.
    Parameter(s):
        none
    Returns:
        weather_data (list): A list which contains time and feels like temperature.
    """
    # See if we already have a valid cached version of the API call
    if os.path.exists(weather_cache):
        with open (weather_cache, "r") as file:
            cache = json.load(file)
            last_update = datetime.fromisoformat(cache["timestamp"]).replace(tzinfo=timezone.utc)
            # will not call the API again until 3 hours after the last time the cache updated
            next_update = last_update + timedelta(hours=3) 
           
           #If cache still valid, return cache - do not call API
            if datetime.now(timezone.utc) < next_update:
                print(f"Using cached weather data (valid until {next_update.strftime('%H:%M')})")
                return cache["weather_data"]

    #If cache is expired or does not exist, call the API
    print ("Fetching new weather data from Met Office API. Please wait...")
   
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes
        # Parse the JSON response
        data = response.json()
        # List to store weather data
        weather_data = []

        # Debug print to inspect the full response structure - uncomment this if weather station location has changed.
        #print("Raw Weather Data:")
        #print(data)

        # Check if 'features' exist and contain the expected data
        if 'features' in data and len(data['features']) > 0:
            time_series = data['features'][0]['properties'].get('timeSeries', [])
            
            # Loop through the time series data and extract the "feels like" temperature
            for period in time_series:
                time = period.get('time')
                feels_like = period.get('feelsLikeTemperature')

                # Store the time and feels like temperature in a dictionary
                if feels_like is not None:
                    weather_data.append({'time': time, 'feelsLikeTemperature': feels_like})
                else:
                    weather_data.append({'time': time, 'feelsLikeTemperature': 'Data not available'})
        else:
            print("No valid features found in the response.")
        # Save new data to cache file    
        with open(weather_cache, "w") as file:
            json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "weather_data": weather_data}, file)
        return weather_data

    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
    except requests.exceptions.RequestException as err:
        print(f"Other error occurred: {err}")
    return [] #Return empty list if there is an error

#search weather_data to confirm temperature is/isn't >22c in the next 48 hours.
def weather_analysis(weather_data, threshold_temp=22):
    """ Analyses the data recieved from the Met Office DataHub. 
    Parameter(s):
        weather_data (list): hourly weather data for chosen weather station - output of get_weather function
        threshold_temp (int): threshold temperature beyond which it is considered 'hot weather'
    Returns: 
        hot weather (bool): returns true if any entry has a feels-like temperature above the threshold 
    """
    for entry in weather_data:
        if isinstance(entry['feelsLikeTemperature'], (int, float)):
            if entry['feelsLikeTemperature'] > threshold_temp:
                print(f"Time: {entry['time']} - Feels like temperature is {entry['feelsLikeTemperature']}°C")
                return True  # Return True if any entry has a feels-like temperature above the threshold
            #else:
                #print(f"Time: {entry['time']} - Feels like temperature is {entry['feelsLikeTemperature']}°C")
        else:
            print(f"Time: {entry['time']} - Feels like temperature data not available.")
    return False  # Return False if no entry exceeds the threshold

# -- Get Data - Tasks from Todoist, Energy levels and working hours from Google Sheets
api_key = os.getenv("TODOIST_API_KEY")
if not api_key:
    raise ValueError("TODOIST_API_KEY not found in environment variables. Please check your .env file.")

# Initialize the Todoist API with the key from the .env file
api = TodoistAPI(api_key)

def parse_personal_and_work_tasks():
    """ Pulls tasks from Todoist. Helper functions parse estimated task duration and task due date, map labels to task attributes.
    Parameters:
        none
    Returns:
        parsed_tasks (list): a list of work/personal tasks parsed from Todoist 
    """
    try:
        # Fetch all tasks
        tasks = api.get_tasks()
        parsed_tasks = []

        def extract_estimated_time(description, default_time=60):
            """ Extracts estimated time from task description or notes. Matches both compact (e.g., '1h', '30m') and natural language (e.g., '1 hour').
            Parameter(s):
                description (string): task description, from Todoist 
                default time (int): default task duration (60 mins) if no task duration is found
            Returns:
                estimated_time (int): task duration (in mins) as parsed from task description in Todoist 
            """
            # Match compact time formats like '1h', '30m'
            match = re.search(r"(\d+)\s*([hm])", description.lower())
            if match:
                value, unit = match.groups()
                return int(value) * 60 if unit == "h" else int(value)

            # Match natural language formats like '1 hour', '30 minutes'
            match = re.search(r"(\d+)\s*(hour|minute)", description.lower())
            if match:
                value, unit = match.groups()
                return int(value) * 60 if "hour" in unit else int(value)

            # Match fractional hours (e.g., '0.5 hours')
            match = re.search(r"(\d*\.\d+)\s*hour", description.lower())
            if match:
                return int(float(match.group(1)) * 60)

            # Return default time if no match is found
            return default_time

        def map_labels_to_attributes(labels):
            """
            Maps task labels to energy level, impact, classification, and type.
            Parameter(s):
                labels: from labels assigned in Todoist
            Returns:
                energy_level (literal): ['high', 'medium', 'low']
                impact (literal): ['very high', 'high', 'medium', 'low']
                classification (literal): ['email', 'admin', 'writing', 'data analysis', 'reading & searching', 'thinking & planning', 'preparing & giving talks']
                task_type (literal): ['Work', 'Personal']
            """
            energy_level = None
            impact = None
            classification = None
            task_type = None
            for label in labels:
                if label == "high_energy":
                    energy_level = "high"
                elif label == "medium_energy":
                    energy_level = "medium"
                elif label == "low_energy":
                    energy_level = "low"

                if label == "veryhigh_impact":
                    impact = "very high"
                elif label == "high_impact":
                    impact = "high"
                elif label == "medium_impact":
                    impact = "medium"
                elif label == "low_impact":
                    impact = "low"

                if label == "emails":
                    classification = "email"
                elif label == "admin":
                    classification = "admin"
                elif label == "writing":
                    classification = "writing"
                elif label == "data_analysis":
                    classification = "data analysis"
                elif label == "reading_searching":
                    classification = "reading & searching"
                elif label == "thinking_planning":
                    classification = "thinking & planning"
                elif label == "giving_talks":
                    classification = "preparing & giving talks"

                if label == "work":
                    task_type = "Work"
                elif label == "personal":
                    task_type = "Personal"

            return energy_level, impact, classification, task_type

        def parse_due_date_or_datetime(task_due):
            """
            Parses the due date or datetime for a task. Handles both full dates (whole-day tasks) and datetime objects.
            Ensures all datetimes are timezone-aware.
            Parameter(s):
                task_due:
            Returns:
                deadline (datetime): returns deadline if task has both a due date and time
                task_end_of_day (datetime): returns task_end_of_day if task only has a due date, and no set time. 23:59:59 is then assigned automatically
            """
            local_tz = pytz.timezone('Europe/London')

            if not task_due:
                return None  # Handle missing due field gracefully

            # Task with datetime
            if hasattr(task_due, 'datetime') and task_due.datetime:
                deadline = parse_datetime(task_due.datetime)  # Call fixed parse_datetime
                return deadline

            # Task with date only
            if hasattr(task_due, 'date') and task_due.date:
                if isinstance(task_due.date, str):
                    task_date = datetime.strptime(task_due.date, '%Y-%m-%d')  # Convert to datetime
                else:
                    task_date = task_due.date

                # Localize date to Europe/London
                if task_date.tzinfo is None:
                    task_date = local_tz.localize(task_date)

                # Assign 23:59:59 as the end of the day
                task_end_of_day = task_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                return task_end_of_day

            return None

        def parse_datetime(date_string):
            """
            Converts a datetime string into a timezone-aware datetime object.
            Assumes the date string is in UTC if no timezone information is present.
            """
            try:
                # Try parsing as a datetime with timezone info
                dt = datetime.fromisoformat(date_string)
                local_tz = pytz.timezone('Europe/London')
                if dt.tzinfo is None:  # Localize naive datetime
                    dt = local_tz.localize(dt)
                else:  # Convert aware datetime to local timezone
                    dt = dt.astimezone(local_tz)
                return dt
            except ValueError:
                print(f"Error parsing datetime: {date_string}")
                return None

        for task in tasks:
            # Only process tasks with 'personal' or 'work' labels
            if "personal" in task.labels or "work" in task.labels:
                # Extract task details
                task_id = task.id
                task_content = task.content
                task_labels = task.labels
                task_due = task.due

                # Fetch notes (comments) for the task
                try:
                    notes = api.get_comments(task_id=task_id, object_type="task")
                    note_contents = [note["content"] for note in notes]
                except Exception as e:
                    print(f"Failed to fetch notes for task '{task_content}': {e}")
                    note_contents = []

                # Parse estimated time from description or notes
                estimated_time = extract_estimated_time(task.description or " ".join(note_contents), default_time=60)

                # Map labels to energy needs and impact
                energy_level, impact, classification, task_type = map_labels_to_attributes(task_labels)

                # Parse the deadline
                deadline = parse_due_date_or_datetime(task_due)

                # Add parsed task data to the list
                parsed_tasks.append({
                    "id": task_id,
                    "name": task_content,
                    "labels": task_labels,
                    "notes": note_contents,
                    "estimated_time": estimated_time,
                    "energy_level": energy_level,
                    "impact": impact,
                    "task_type": task_type,
                    "classification": classification,
                    "deadline": deadline,
                })

        return parsed_tasks
  
    except Exception as e:
        print(f"An error occurred while parsing tasks: {e}")
        return []
    
#Fetch working hours and energy levels.
def fetch_working_hours_and_energy_levels(sheets_service, weather_analysis=False):
    """
    Fetches working hours and energy levels from Google Sheets. Helper functions converts 1-10 into low, medium, high and generates an energy profile 
    for the next 28 days to allow future scheduling of tasks. 
    Parameter(s):
        sheets_service: Google Sheets service instance
        weather_analysis (bool): Default is false. True if hot weather has been detected.
    Returns:
        energy_profile (dict): Uses date as the key, holds timeslot start and end, and it's associated energy value as string.
    """
    standard_sheet_id = "1ILvTyuMjPQ0NiC1dqF_zi14CxKBvhiSUu0yhO0SEQyk"
    hot_weather_sheet_id = "1YZfDpX3bBYqDqwZdEq6O7yrK34PcTLgiX7p3d2mDCW0"
    range_name = "Sheet1!A2:D"  # Range to fetch from the Google Sheet
    
    # Choose the appropriate sheet based on the weather analysis condition
    sheet_id = hot_weather_sheet_id if weather_analysis else standard_sheet_id
    
    try:
        sheet = sheets_service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
        rows = sheet.get("values", [])
        
        energy_profile = {}

        def convert_energy_level(energy_level):
            try:
                energy_level = int(energy_level)
                if 1 <= energy_level <= 3:
                    return "low"
                elif 4 <= energy_level <= 6:
                    return "medium"
                elif 7 <= energy_level <= 10:
                    return "high"
                return "unknown"
            except ValueError:
                print(f"Invalid energy level value: {energy_level}")
                return "unknown"

        # Get today's date and the date 28 days from today
        today = datetime.today()
        date_list = [today + timedelta(days=i) for i in range(28)]

        # Initialize the energy profile with each specific date as a key
        for date in date_list:
            energy_profile[date.strftime('%Y-%m-%d')] = []

        # Populate the energy profile from the Google Sheets data
        for row in rows:
            if len(row) >= 4:
                day_name = row[0]  # Expecting the day name (e.g., "Monday")
                time_range = row[1]
                task_type = row[2]

                # Find all matching dates for the given day name
                matching_dates = [date for date in date_list if date.strftime("%A") == day_name]

                if not matching_dates:
                    print(f"Skipping row with day name not in the next 28 days: {day_name}")
                    continue

                # Handle the time range parsing
                if not time_range or " - " not in time_range:
                    print(f"Skipping row with invalid time range format: {row}")
                    continue

                start_time_str, end_time_str = time_range.split(' - ')

                for current_date in matching_dates:
                    try:
                        start_time = local_tz.localize(datetime.strptime(f"{current_date.strftime('%Y-%m-%d')} {start_time_str}", "%Y-%m-%d %H:%M"))
                        end_time = local_tz.localize(datetime.strptime(f"{current_date.strftime('%Y-%m-%d')} {end_time_str}", "%Y-%m-%d %H:%M"))
                    except ValueError as e:
                        print(f"Skipping row due to invalid time format: {time_range} ({e})")
                        continue

                    # Add the time range to the energy profile for the specified date
                    energy_profile[current_date.strftime('%Y-%m-%d')].append({
                        "time_range": (start_time, end_time),
                        "task_type": task_type,
                        "energy_level": convert_energy_level(row[3])
                    })
        return energy_profile

    except Exception as e:
        print(f"An error occurred while fetching data: {e}")
        return {}
    
## -- Pulling meetings from Google Calendar, adding travel events/ screen-free time
def fetch_calendar_events(calendar_service, time_min=None, time_max=None):
    """ 
    Fetches events from Google Calendar and categorizes them into meetings, tasks, travel, and screen-free time.
    
    Parameters:
        calendar_service: Google Calendar service instance
        time_min (datetime): Start of time range to pull events (default: now)
        time_max (datetime): End of time range to pull events (default: 28 days ahead)
    
    Returns:
        meetings (dict): {meeting_id: event_data}
        tasks (dict): {task_id: event_data}
        travel_times (dict): {meeting_id_before/after: (event_id, start_time, end_time)}
        screen_free_times (dict): {meeting_id: (event_id, start_time, end_time)}
        occupied_slots (list): [(start_time, end_time)] - List of occupied time slots (excludes tasks)
    """
    local_tz = pytz.timezone('Europe/London')

    if not time_min:
        time_min = datetime.now(timezone.utc).isoformat()
    if not time_max:
        time_max = (datetime.now(timezone.utc) + timedelta(days=28)).astimezone(local_tz).isoformat()

    meetings = {}
    tasks = {}
    travel_times = {}
    screen_free_times = {}
    occupied_slots = []

    page_token = None
    all_events = [] #store all the events first, then iterate through. 
    while True:
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime',
            pageToken=page_token
        ).execute()

        all_events.extend(events_result.get('items', []))
        page_token = events_result.get('nextPageToken')
        if not page_token:
            break

        # Process Meetings first - we need this because meeting info required to check travel/screen_free time, otherwise none-type error.
    for event in all_events:
        event_summary = event.get('summary', '').lower()
        description = event.get('description', '').lower()
        event_id = event.get('id', '')
        start_time, end_time = parse_event_datetime(event)

        if not start_time or not end_time:
            print(f"Warning: Skipping event with missing start or end time → {event_summary}")
            continue

        # Meetings
        if ('scheduled by task scheduler' not in description and 
            'travel' not in event_summary and 
            'screen-free time' not in event_summary):
            
            meetings[event_id] = event
            occupied_slots.append((start_time, end_time))

    for event in all_events:
        event_summary = event.get('summary', '').lower()
        description = event.get('description', '').lower()
        event_id = event.get('id', '')
        start_time, end_time = parse_event_datetime(event)

        if not start_time or not end_time:
            continue

        # Categorise Tasks
        if "scheduled by task scheduler" in description.lower():
            task_id_match = re.search(r"task id:\s*(\S+)", description)
            if task_id_match:
                task_id = task_id_match.group(1).strip()
                tasks[task_id] = event  # Store event under task ID
            continue

        # Categorise Travel Time
        if "travel" in event_summary.lower():
            parent_event_id_match = re.search(r"parent meeting id:\s*(\S+)", description)
            parent_event_id = parent_event_id_match.group(1).strip() if parent_event_id_match else None
                
            if parent_event_id:
                meeting_event = meetings.get(parent_event_id)
                if meeting_event:
                    meeting_start_time, _ = parse_event_datetime(meeting_event)
                else:
                    meeting_start_time = None
                if start_time < meeting_start_time:
                    travel_times[f"{parent_event_id}_before"] = (event["id"], start_time, end_time)
                    occupied_slots.append((start_time, end_time))  
                else:
                    travel_times[f"{parent_event_id}_after"] = (event["id"], start_time, end_time)
                    occupied_slots.append((start_time, end_time))  
                    continue 

        # Categorise Screen-Free Time
        if "screen-free time" in event_summary.lower():
            parent_event_id_match = re.search(r"parent meeting id:\s*(\S+)", description)
            parent_event_id = parent_event_id_match.group(1).strip() if parent_event_id_match else None

            if parent_event_id:
                screen_free_times[parent_event_id] = (event_id, start_time, end_time)
                occupied_slots.append((start_time, end_time))  
    return meetings, tasks, travel_times, screen_free_times, occupied_slots

def ensure_datetime(value):
    """Ensure a value is a datetime object and make it timezone-aware with GMT/BST handling."""
    local_tz = pytz.timezone('Europe/London')

    if isinstance(value, str):
        try:
            dt = parser.isoparse(value)
            # Ensure datetime is aware and convert to local timezone
            if dt.tzinfo is None:  # Naive datetime
                dt = local_tz.localize(dt)  # Localize to Europe/London
            else:
                dt = dt.astimezone(local_tz)  # Convert to local timezone
            return dt
        except ValueError:
            raise ValueError(f"Invalid datetime string: {value}")
    elif isinstance(value, datetime):
        # Handle timezone-awareness for datetime objects
        if value.tzinfo is None:
            value = local_tz.localize(value)
        else:
            value = value.astimezone(local_tz)
        return value
    else:
        raise TypeError(f"Expected datetime object or string, got {type(value).__name__}")
    
def parse_event_datetime(event):
    """Parse start and end times from a calendar event and ensure they are datetime objects.
    Parameter(s):
        event(dict): a single entry in list of events (e.g., meetings) that occupy specific timeslots
    Returns:
        start_time (datetime): event start time
        end_time (datetime): event end time
    """
    try:
        start_time = event['start'].get('dateTime') or event['start'].get('date')
        end_time = event['end'].get('dateTime') or event['end'].get('date')

        start_time = parser.isoparse(start_time)
        end_time = parser.isoparse(end_time)

        local_tz = pytz.timezone('Europe/London')

        if start_time.tzinfo is None:
            start_time = local_tz.localize(start_time, is_dst=None)
        else:
            start_time = start_time.astimezone(local_tz)

        if end_time.tzinfo is None:
            end_time = local_tz.localize(end_time, is_dst=None)
        else:
            end_time = end_time.astimezone(local_tz)
        return start_time, end_time
    
    except Exception as e:
        print(f"Error parsing event datetime: {e}")
        return None, None
        
# Adds travel events before and after a meeting if it has a location
def add_travel_event(calendar_service, task_name,event_id, travel_start, travel_end, location):
    """
    If a meeting has a location, then add travel time as separate events before/after the meeting
    Parameter(s):
        calendar_service: Google Calendar service instance
        task_name (str): task name
        travel_start (datetime): start time of travel
        travel_end (datetime): end time of travel event
        location (str): event location
    Returns:
        new event: Travel events are created in Google Calendar
    """
    try:
        # Create a travel event
        event = {
            'summary': f'Travel {task_name}',
            'description': f'Travel time to/from {location} Parent Meeting ID: {event_id}',
            'start': {'dateTime': travel_start.isoformat(), 'timeZone': 'Europe/London'},
            'end': {'dateTime': travel_end.isoformat(), 'timeZone': 'Europe/London'},
        }
        # Insert event into Google Calendar
        calendar_service.events().insert(calendarId='primary', body=event).execute()
        return travel_start, travel_end

    except Exception as e:
        # Log errors and return None
        log_message("ERROR", f"Failed to add travel event: {e}")
        return None, None
    
def is_virtual_meeting(event):
    """ Parses event description and location to determine if this is a virtual meeting, with case insensitive matching for keywords
    Parameter(s):
        event(dict): a single entry in list of events (e.g., meetings) that occupy specific timeslots
    Returns:
        is_virtual_meeting (bool): returns true if one of a set of keywords is found in event description/location 
    """
    virtual_keywords = ['zoom', 'google meet', 'teams', 'skype', 'webex', 'attendanywhere']
    description = event.get('description', '').lower()
    
    # Check if any of the virtual keywords are in the description
    if any(keyword in description for keyword in virtual_keywords):
        return True
    
    # Optionally, check the location field for virtual meeting indicators
    location = event.get('location', '').lower()
    if any(keyword in location for keyword in virtual_keywords):
        return True
    
    # If neither description nor location indicates a virtual meeting, return False
    return False

def add_rest_period(calendar_service, event_id, end_time):
    """ Adds a 15-minute screen-free period following a virtual meeting. Screen-free time added as a separate calendar event.
    Parameter(s):
        calendar_service: Google Calendar instance
        end_time (datetime): event end time (start time of screen-free time)
    Returns:
        new event: Travel events are created in Google Calendar
    """
    local_tz = pytz.timezone('Europe/London')
    if end_time.tzinfo is None:
        end_time = local_tz.localize(end_time)
    else:
        end_time = end_time.astimezone(local_tz)

    # Define rest period
    rest_start_time = end_time
    rest_end_time = rest_start_time + timedelta(minutes=15)

    # Create event
    event = {
        'summary': 'Screen-Free Time',
        'description': f'Take a short break after the virtual meeting.\n Parent Meeting ID: {event_id}' ,
        'start': {'dateTime': rest_start_time.isoformat(), 'timeZone': 'Europe/London'},
        'end': {'dateTime': rest_end_time.isoformat(), 'timeZone': 'Europe/London'},
    }

    try:
        calendar_service.events().insert(calendarId='primary', body=event).execute()
    except Exception as e:
        log_message("ERROR", f"Failed to add screen-free time: {e}")

    # Return start and end times
    return rest_start_time, rest_end_time

def update_event(calendar_service, event_id, new_start, new_end):
    """Update travel events or screen-free time in Google Calendar when associated meeting moves
    Parameter(s):
        calendar_service: Google Calender service instance
        event_id (dict): 
        new_start ():
        new_end ():
    Returns:
        updated_event: Updated Google Calendar event
    """
    try:
        # Fetch the existing event
        event = calendar_service.events().get(calendarId='primary', eventId=event_id).execute()
        # Detect and preserve original timezone
        event_timezone = event['start'].get('timeZone', 'UTC')  # Default to UTC if not set
        event['start'] = {
            'dateTime': new_start.isoformat(),
            'timeZone': event_timezone
        }
        event['end'] = {
            'dateTime': new_end.isoformat(),
            'timeZone': event_timezone
        }

        # Send the update request
        updated_event = calendar_service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
        print(f"Updated event {event_id}: {new_start} to {new_end}")
        return updated_event

    except Exception as e:
        print(f"Failed to update event {event_id}: {e}")
        return None

# Handle meeting with location and add travel time and rest period if virtual
def handle_meeting_with_location(calendar_service, event, travel_times, screen_free_times, location=None, travel_time=30, occupied_slots=[]):
    """ Add travel time (if in person) or rest period after meeting (if virtual).
    Parameter(s):
        calendar_service: Google Calendar service instance
        event (dict): a single entry in list of events (e.g., meetings) that occupy specific timeslots
        existing_travel (dict): a set of (start_time, end_time) tuples for travel events
        existing_rest (dict): a set of (start_time, end_time) tuples for screen-free events
        location (str): event location
        travel_time (int): length of travel to/from event. Default 30 mins
        occupied slots (dict): a dictionary of timeslots already occupied by fixed events
    Returns:
        new event: creates travel time (in person) or screen-free time (if virtual)
    """
    # Set timezone
    local_tz = pytz.timezone('Europe/London')

    # Parse start and end times of the event
    start_time, end_time = parse_event_datetime(event)

    if not start_time or not end_time:
        print(f"Warning: Skipping event with missing start or end time → {event.get('summary', 'Unnamed Event')}")
        return

    # Ensure times are timezone-aware
    if start_time.tzinfo is None:
        start_time = local_tz.localize(start_time)
    if end_time.tzinfo is None:
        end_time = local_tz.localize(end_time)

    event_id = event.get('id', '')

    # Handle Virtual Meetings - Add a Rest Period**
    if is_virtual_meeting(event):
        rest_start, rest_end = end_time, end_time + timedelta(minutes=15)
        existing_rest_event = screen_free_times.get(event_id)

        if existing_rest_event:
            rest_event_id, old_start, old_end = existing_rest_event
            if (old_start, old_end) != (rest_start, rest_end):
                update_event(calendar_service, rest_event_id, rest_start, rest_end)
                log_message("INFO", f"Updated screen-free time {rest_event_id}.")
                occupied_slots.remove((old_start, old_end))
                occupied_slots.append((rest_start, rest_end))
            else:
                log_message("INFO", f"Screen-free time for {event_id} is already correct. Skipping update.")
        else:
            rest_event_id = add_rest_period(calendar_service, event_id, end_time)
            screen_free_times[event_id] = (rest_event_id, rest_start, rest_end)
            occupied_slots.append((rest_start, rest_end))
            log_message("INFO", f"Created screen-free time for {event_id}: {rest_start} to {rest_end}")

    # Handle In-Person Meetings - Add Travel Time**
    else:
        travel_before_start, travel_before_end = start_time - timedelta(minutes=travel_time), start_time
        existing_travel_before = travel_times.get(f"{event_id}_before")

        if existing_travel_before:
            travel_event_before_id, old_start, old_end = existing_travel_before
            if (old_start, old_end) != (travel_before_start, travel_before_end):
                update_event(calendar_service, travel_event_before_id, travel_before_start, travel_before_end)
                log_message("INFO", f"Updated travel event (before meeting) {travel_event_before_id}.")
                if (old_start, old_end) in occupied_slots:
                    occupied_slots.remove((old_start, old_end))
                else:
                    print(f"WARNING: Attempted to remove {old_start} to {old_end} but it was not found in occupied_slots!")
                occupied_slots.append((travel_before_start, travel_before_end))
            else:
                log_message("INFO", f"Travel event (before meeting) for {event_id} is already correct. Skipping update.")
        else:
            travel_event_before_id = add_travel_event(calendar_service, "to Meeting", event_id, travel_before_start, travel_before_end, location)
            travel_times[f"{event_id}_before"] = (travel_event_before_id, travel_before_start, travel_before_end)
            occupied_slots.append((travel_before_start, travel_before_end))
            log_message("INFO", f"Created travel event (before meeting) for {event_id}: {travel_before_start} to {travel_before_end}")

        travel_after_start, travel_after_end = end_time, end_time + timedelta(minutes=travel_time)
        existing_travel_after = travel_times.get(f"{event_id}_after")

        if existing_travel_after:
            travel_event_after_id, old_start, old_end = existing_travel_after
            if (old_start, old_end) != (travel_after_start, travel_after_end):
                update_event(calendar_service, travel_event_after_id, travel_after_start, travel_after_end)
                log_message("INFO", f"Updated travel event (after meeting) {travel_event_after_id}.")
                occupied_slots.remove((old_start, old_end))
                occupied_slots.append((travel_after_start, travel_after_end))
            else:
                log_message("INFO", f"Travel event (after meeting) for {event_id} is already correct. Skipping update.")
        else:
            travel_event_after_id = add_travel_event(calendar_service, "from Meeting", event_id, travel_after_start, travel_after_end, location)
            travel_times[f"{event_id}_after"] = (travel_event_after_id, travel_after_start, travel_after_end)
            occupied_slots.append((travel_after_start, travel_after_end))
            log_message("INFO", f"Created travel event (after meeting) for {event_id}: {travel_after_start} to {travel_after_end}")

# -- Prioritisation of tasks
# Mapping importance to a numerical value
impact_mapping = {
    "very high": 4,
    "high": 3,
    "medium": 2,
    "low": 1
}

# Define weights for prioritization
weights = {
    "T": 0.2,  # Time required
    "E": 0.4,  # Energy required
    "I": 0.3,  # Impact
    "D": 0.1   # Deadline proximity
}

# Define max time for normalization
max_time = 480  # In minutes - 8 hours
maximum_energy = 3  # Energy level on a scale of 1 to 3, A slot can have at maximum "high/3" energy.

# Function to calculate task score
def calculate_task_score(task):
    """
    Calculates priority score for each task as a function of time needed, energy required, the tasks impact
    and the proximity to its deadline.
    Parameter(s):
        task(dict): task from Todoist 
    Returns:
        score (int): Task score.
    """
    
    # Calculate the task score 
    time_needed = task.get("estimated_time", 60)  # Default to 60 mins if missing
    energy_required = {"low": 1, "medium": 2, "high": 3}.get(task.get("energy_level", "medium"), 0)
    impact = impact_mapping.get(task.get("impact", "low").lower(), 0)

    # Handle deadline as either string or datetime
    deadline = task.get("deadline")
    if deadline is None:
        deadline_days = float('inf')  # No deadline means very low urgency
    else:
        if isinstance(deadline, str):
            deadline = datetime.strptime(deadline, "%Y-%m-%d")

        # Ensure both deadline and current time are timezone-aware
        if deadline.tzinfo is None:
            deadline = local_tz.localize(deadline)  # Localize naive time
        else:
            deadline = deadline.astimezone(local_tz)

        if datetime.now().tzinfo is None:  # Naive current time, make it aware
            current_time = datetime.now(pytz.utc).astimezone(local_tz)

        # Calculate difference between deadline and current time (in days)
        deadline_days = (deadline - current_time).days

    # Normalize scores
    time_score = (time_needed / max_time) * weights["T"]
    energy_score = (energy_required / maximum_energy) * weights["E"]
    impact_score = impact * weights["I"]
        # Calculate the deadline score
    if deadline_days >= 0: # Task is not overdue
        deadline_score = (1 / (deadline_days + 1)) * weights["D"]  
    else:
        deadline_score = 2  # Task is overdue, fixed value for high urgency

    # Composite score
    score = time_score + energy_score + impact_score + deadline_score
    return score

def convert_energy_level_to_int(energy_level):
    """Converts energy level from string to integer for comparison."""
    energy_mapping = {'low': 1, 'medium': 2, 'high': 3}
    return energy_mapping.get(energy_level.lower(), 0)

def get_available_timeslots(energy_profile, occupied_slots, task_type, task_energy_level, task_deadline):
    """
    Determines available timeslots by comparing energy profile and occupied slots from the calendar,
    and filters them based on the task's energy level, deadline, and current time.
    Handles partially available slots and updates the available slots accordingly.
    Parameter(s):
        energy_profile (dict): Uses date as the key, holds timeslot start and end, and it's associated energy value as string.
        occupied_slots (list): a list of immovable events (e.g., meetings) that occupy specific timeslots
        task_type(literal): [work, personal]
        task_energy_level (literal): [low, medium, high]
        task_deadline (datetime): If deadline == none, then 31st-Dec-9999 is assigned within this function
    Returns:
        availabile_timeslots (dict): timeslots available for each task

    """
    local_tz = pytz.timezone('Europe/London')

    # Get the current time and convert to local timezone
    current_time = datetime.now(timezone.utc).astimezone(local_tz)

    # Ensure task_deadline is timezone-aware
    if task_deadline is None:
        task_deadline = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc).astimezone(local_tz)
    elif task_deadline.tzinfo is None:
        task_deadline = local_tz.localize(task_deadline)

    available_timeslots = {}

    for day, slots in energy_profile.items():
        available_slots = []  # Store available slots for this day

        for slot in slots:
            slot_start, slot_end = slot["time_range"]
            slot_start, slot_end = ensure_datetime(slot_start), ensure_datetime(slot_end)

            # Ensure slot times are timezone-aware
            if slot_start.tzinfo is None:
                slot_start = local_tz.localize(slot_start)
            if slot_end.tzinfo is None:
                slot_end = local_tz.localize(slot_end)

            # Skip Slots Outside Valid Time Range**
            if slot_end <= current_time or slot_start >= task_deadline:
                continue

            # Remove Fully Occupied Slots Before Processing
            fully_available_parts = [(slot_start, slot_end)]
            for occupied_start, occupied_end in occupied_slots:
                temp_parts = []
                for free_start, free_end in fully_available_parts:
                    if free_start >= occupied_start and free_end <= occupied_end:
                        continue  # Skip fully occupied slots
                    elif free_start < occupied_start < free_end:
                        temp_parts.append((free_start, occupied_start))
                    elif free_start < occupied_end < free_end:
                        temp_parts.append((occupied_end, free_end))
                    else:
                        temp_parts.append((free_start, free_end))
                fully_available_parts = temp_parts

            # Process Only the Available Portions**
            for free_start, free_end in fully_available_parts:
                slot_duration = free_end - free_start
                slot_energy_level = convert_energy_level_to_int(slot["energy_level"])
                task_energy_level_int = convert_energy_level_to_int(task_energy_level)

                # Check Energy Level & Task Type
                if slot_energy_level >= task_energy_level_int and slot["task_type"] == task_type:
                    if slot_duration >= timedelta(minutes=task["estimated_time"]):
                        available_slots.append((free_start, free_end))
                    else:
                        # Handle small slots in 15-minute chunks for low/medium energy tasks, 30 minute chunks for high-energy tasks
                        if task_energy_level_int != 3:
                            current_chunk_start = free_start
                            chunks = []
                            while current_chunk_start < free_end:
                                current_chunk_end = min(current_chunk_start + timedelta(minutes=15), free_end)
                                chunks.append((current_chunk_start, current_chunk_end))
                                current_chunk_start = current_chunk_end
                        else:
                            current_chunk_start = free_start
                            chunks = []
                            while current_chunk_start < free_end:
                                current_chunk_end = min(current_chunk_start + timedelta(minutes=30), free_end)
                                chunks.append((current_chunk_start, current_chunk_end))
                                current_chunk_start = current_chunk_end
                        # Merge consecutive chunks into a larger slot
                        if chunks:
                            available_slots.append((chunks[0][0], chunks[-1][1]))

        if available_slots:
            available_timeslots[day] = available_slots

    #for day, slots in available_timeslots.items():
        #print(f"  {day}: {slots}")

    return available_timeslots

def print_suitable_timeslots(energy_profile, occupied_slots, task_type, task_energy_level, task_deadline):
    """ Prints all possible suitable timeslots for a given task that exist between now and task deadline. Mostly used for debugging purposes.
    Parameter(s):
    energy_profile (dict): data from Google Sheets, then extended to the next 28 days through fetch_working_hours_and_energy_levels
    calendar_events: a list of immovable events (e.g., meetings) that occupy specific timeslots
    task_type (literal): ['Work', 'Personal']
    task_energy_level (literal): [low, medium, high]
    task_deadline (datetime): task deadline (from Todoist). If no time set, 23:59:59 is assigned through get_available_timeslots
        """
    suitable_timeslots = get_available_timeslots(energy_profile, occupied_slots, task_type, task_energy_level, task_deadline)
    
    print("Suitable timeslots for the task:")
    for day, slots in suitable_timeslots.items():
        print(f"Day: {day}")
        for start_time, end_time in slots:
            print(f"  From {start_time} to {end_time}")

def merge_available_slots(slots):
    """
    Merges consecutive timeslots if there is no gap between them.
    Args:
        slots (list): A list of (start_time, end_time) tuples.
    Returns:
        list: A merged list of available time slots.
    """
    if not slots:
        return []

    slots.sort()  # Ensure slots are sorted by start time
    merged_slots = [slots[0]]

    for current_start, current_end in slots[1:]:
        last_start, last_end = merged_slots[-1]

        if current_start == last_end:  # If consecutive, merge
            merged_slots[-1] = (last_start, current_end)
        else:
            merged_slots.append((current_start, current_end))

    return merged_slots

def insert_breaks(occupied_slots):
    """
    Inserts breaks (morning, lunch, afternoon) while ensuring lunch is taken in a single block.
    """
    local_tz = pytz.timezone('Europe/London')

    # Define Fixed Break Windows
    # Morning break between 9.30am and 10am
    morning_break_window = (datetime.now().replace(hour=9, minute=30, second=0, microsecond=0, tzinfo=local_tz),
                            datetime.now().replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=local_tz))
    # Lunch between 12 and 2pm
    lunch_window = (datetime.now().replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=local_tz),
                    datetime.now().replace(hour=14, minute=0, second=0, microsecond=0, tzinfo=local_tz))
    # Afternoon break between 3.30pm and 4pm
    afternoon_break_window = (datetime.now().replace(hour=15, minute=30, second=0, microsecond=0, tzinfo=local_tz),
                              datetime.now().replace(hour=16, minute=0, second=0, microsecond=0, tzinfo=local_tz))

    # Insert Morning & Afternoon Breaks (15 min)
    breaks_to_schedule = [
        (morning_break_window[0], morning_break_window[0] + timedelta(minutes=15)),
        (afternoon_break_window[0], afternoon_break_window[0] + timedelta(minutes=15))
    ]

    # Determine the largest available lunch slot within 12:00 - 14:00
    available_lunch_slots = []
    lunch_start, lunch_end = lunch_window
    current_start = lunch_start

    while current_start + timedelta(minutes=30) <= lunch_end:
        potential_end = min(current_start + timedelta(minutes=60), lunch_end)

        # Ensure no conflicts with occupied slots
        conflicts = any(
            occupied_start < potential_end and occupied_end > current_start
            for occupied_start, occupied_end in occupied_slots
        )

        if not conflicts:
            available_lunch_slots.append((current_start, potential_end))

        # Move to the next possible slot
        current_start += timedelta(minutes=15)

    # Select the best lunch duration available (preferring 60 min, then 45, then 30)
    if available_lunch_slots:
        best_lunch_slot = max(available_lunch_slots, key=lambda x: x[1] - x[0])  # Pick longest slot
    else:
        print("Warning: No available lunch slot found!")
        best_lunch_slot = (lunch_start, lunch_start + timedelta(minutes=30))  # Force minimum 30 min

    breaks_to_schedule.append(best_lunch_slot)  # Add lunch as a single block

    # Merge breaks into occupied slots
    occupied_slots.extend(breaks_to_schedule)

    return occupied_slots

# -- Task scheduling logic. Using Greedy Algorithm
def schedule_tasks(tasks, available_timeslots, occupied_slots, existing_tasks):
    """
    Schedules tasks using a greedy approach, ensuring tasks are scheduled into available timeslots
    without overlap with occupied slots.
    Parameter(s):
        tasks (list): list of tasks from todoist 
        available_timeslots (dict): dictionary of unoccupied slots of equal or greater energy level and correct type [work, personal].
        occupied_slots (list): list of occupied slots from Google Calendar (e.g., meetings and other immovable tasks) 
    Returns:
        scheduled_tasks (list):list of tasks that have been scheduled with their timeslots. 
    """
    scheduled_tasks = []
    local_tz = pytz.timezone('Europe/London')

    for task in tasks:
        task_name = task["name"]
        task_id = task["id"]
        task_duration = timedelta(minutes=task["estimated_time"])

        #if task_id in existing_tasks:
            #existing_event = existing_tasks[task_id]
            #start_time, end_time = parse_event_datetime(existing_event)
            #scheduled_tasks.append({
                #"task_id": task_id,
                #"task_name": task_name,
                #"start_time": start_time,
                #"end_time": end_time
            #})
            #log_message("INFO", f"Task '{task_name}' already scheduled from {start_time} to {end_time}. Skipping rescheduling.")
            #continue

        # Skip if no available timeslots exist
        if task_id not in available_timeslots:
            print(f"No available timeslots found for task '{task_name}', ID: {task_id}")
            continue

        task_slots = available_timeslots[task_id]

        remaining_time = task_duration
        scheduled_parts = []

        for day, slots in task_slots.items():
            # Sort slots in ascending order before processing
            sorted_slots = sorted(slots, key=lambda x: x[0])

            for slot_start, slot_end in sorted_slots:
                available_time = slot_end - slot_start

                if available_time <= timedelta(minutes=0):
                    continue  # Skip invalid slots

                # Check if the slot conflicts with occupied slots
                conflicts = any(
                    occupied_start < slot_end and occupied_end > slot_start
                    for occupied_start, occupied_end in occupied_slots
                )

                if conflicts:
                    continue  # Skip this slot

                # If a single slot fits the entire task, schedule it
                if available_time >= remaining_time:
                    task_start = slot_start
                    task_end = slot_start + remaining_time
                    scheduled_parts.append((task_start, task_end))
                    occupied_slots.append((task_start, task_end))
                    print(f" Scheduling {task_name} in one slot from {task_start} to {task_end}")
                    remaining_time = timedelta(minutes=0)
                    break  # Task fully scheduled

                else:  # Need to split task across multiple slots
                    task_start = slot_start
                    task_end = slot_end
                    scheduled_parts.append((task_start, task_end))
                    occupied_slots.append((task_start, task_end))
                    remaining_time -= available_time
                    print(f"  Splitting {task_name}, scheduling part from {task_start} to {task_end}")

            if remaining_time == timedelta(minutes=0):
                break  # Task fully scheduled

        # Prevent duplicate scheduling by adding each scheduled part only once
        for part_start, part_end in scheduled_parts:
            if not any(
                task["start_time"] == part_start and task["end_time"] == part_end
                for task in scheduled_tasks
            ):
                scheduled_tasks.append({
                    "task_id": task_id,
                    "task_name": task_name,
                    "start_time": part_start,
                    "end_time": part_end
                })
        
        if scheduled_parts:
            print(f"Task {task_name} scheduled in {len(scheduled_parts)} part(s).")

        if remaining_time > timedelta(minutes=0):
            print(f"Task {task_name} could not be fully scheduled. Remaining time: {remaining_time}")

    return scheduled_tasks

def merge_scheduled_tasks(scheduled_tasks):
    """
    Merges consecutive scheduled tasks into larger slots if they belong to the same task and day.
    Parameter(s):
        scheduled_tasks (list): list of scheduled tasks with their date, start and end times.
    Returns:
        merged_tasks (list): optimized list of scheduled tasks with consecutive slots merged.
    """
    if not scheduled_tasks:
        return []

    scheduled_tasks.sort(key=lambda x: (x["task_name"], x["start_time"].date(), x["start_time"]))
    merged_tasks = []
    
    current_task = scheduled_tasks[0]
    
    for next_task in scheduled_tasks[1:]:
        # Check if the next task is consecutive and belongs to the same task and day
        if (
            next_task["task_name"] == current_task["task_name"]
            and next_task["start_time"].date() == current_task["start_time"].date()
            and next_task["start_time"] == current_task["end_time"]
        ):
            # Extend the current task's end_time
            current_task["end_time"] = next_task["end_time"]
        else:
            # Add the current task to merged_tasks and mark occupied
            merged_tasks.append(current_task)
            occupied_slots.append((current_task["start_time"], current_task["end_time"])) 
            current_task = next_task

    # Add the last task
    merged_tasks.append(current_task)
    occupied_slots.append((current_task["start_time"], current_task["end_time"])) 

    return merged_tasks

def merge_overlapping_intervals(intervals):
    """
    Merge overlapping or adjacent intervals in a list of time ranges.
    Parameter(s):
        intervals (list): list of tuples (start_time, end_time).
    Returns:
        Merged intervals (list): list of tuples (start_time, end_time).
    """
    if not intervals:
        return []

    # Sort intervals by start time
    intervals.sort(key=lambda x: x[0])
    merged_intervals = [intervals[0]]

    for current in intervals[1:]:
        prev_start, prev_end = merged_intervals[-1]
        curr_start, curr_end = current

        # Check for overlap or adjacency
        if curr_start <= prev_end:  # Overlap or adjacent
            merged_intervals[-1] = (prev_start, max(prev_end, curr_end))  # Merge
        else:
            merged_intervals.append(current)

    return merged_intervals

# -- Colour settings for Google Calendar tasks -- 
#colour Name	ID
#Lavender	    1
#Sage	        2
#Grape	        3
#Flamingo	    4
#Banana	        5
#Tangerine	    6
#Peacock	    7
#Graphite	    8
#Blueberry	    9
#Basil	        10
#Tomato	        11

colour_mapping = {

    "emails": "3",  
    "admin": "9",  
    "writing": "7",  
    "data analysis": "10",
    "thinking_planning": "2",
    "reading_searching": "6",
    "giving_talks": "4",
}

def schedule_event(calendar_service, task_name, start_time, end_time, labels, task_id):
    """
    Schedule an event in Google Calendar with a specific colour based on task labels.
    Parameter(s):
        calendar_service: Google Calendar service instance.
        task_name (str): task name.
        start_time (datetime): etart time of the event.
        end_time (datetime): end time of the event.
        labels (list): list of labels associated with the task.
    Returns:
        new event: creates new event in Google Calendar
    """
    # Determine the colourId based on task labels
    task_colour = None
    for label in labels:
        if label in colour_mapping:
            task_colour = colour_mapping[label]
            break  # Use the first matching color

    # Build the event body
    event = {
        'summary': task_name,
        'description': f'Scheduled by task scheduler \n Task ID: {task_id}',
        'start': {'dateTime': start_time.astimezone(local_tz).isoformat(), 'timeZone': 'Europe/London'},
        'end': {'dateTime': end_time.astimezone(local_tz).isoformat(), 'timeZone': 'Europe/London'},
        'colorId': task_colour,  # Set the event colour
    }

    try:
        calendar_service.events().insert(calendarId='primary', body=event).execute()
        print(f"Scheduled: {task_name} from {start_time} to {end_time}")
    except Exception as e:
        print(f"Failed to schedule task '{task_name}': {e}")

def manage_calendar_events(calendar_service, scheduled_tasks, existing_tasks):
    """
    Dynamically manage events in Google Calendar based on scheduled tasks.
    Parameter(s):
        calendar service: Google Calendar service instance.
        scheduled_tasks (list): list of scheduled tasks with their date, start and end times.
        parsed_tasks (list): list of work/personal tasks parsed from Todoist 
    """
    for task in scheduled_tasks:
            task_id = task["task_id"]
            start_time = task["start_time"]
            end_time = task["end_time"]

            # Match Todoist task using task ID
            matching_task = next((t for t in parsed_tasks if t["id"] == task_id), None)
            if not matching_task:
                continue  # Skip tasks without matching details

            task_name = matching_task["name"]
            labels = matching_task["labels"]

            # Check if the task already exists in the calendar
            if task_id in existing_tasks:
                existing_event = existing_tasks[task_id]
                existing_start = parser.isoparse(existing_event['start']['dateTime'])
                existing_end = parser.isoparse(existing_event['end']['dateTime'])

                # Update event only if start or end times differ
                if existing_start != start_time or existing_end != end_time:
                    print(f"Updating event for task '{task_name}'...")

                    # Prepare updated event data
                    updated_event = {
                        'summary': task_name,
                        'start': {'dateTime': start_time.astimezone(local_tz).isoformat(), 'timeZone': 'Europe/London'},
                        'end': {'dateTime': end_time.astimezone(local_tz).isoformat(), 'timeZone': 'Europe/London'},
                        'description': existing_event.get('description', ''),  # Handle missing description
                        'colorId': next((colour_mapping.get(label) for label in labels if label in colour_mapping), None),
                    }

                    # Push the update to Google Calendar
                    calendar_service.events().update(
                        calendarId='primary',
                        eventId=existing_event['id'],
                        body=updated_event
                    ).execute()
            else:
                # No existing event — create a new one
                schedule_event(calendar_service, task_name, start_time, end_time, labels, task_id)


if __name__ == "__main__":
    code_start = time.time()
    try:
        # 🔄 Refresh token if needed
        creds = refresh_token_if_needed()
        if creds:
            log_message("INFO", "Token refreshed successfully.")
        else:
            log_message("ERROR", "Authentication required!")

        # ✅ Authenticate services
        sheets_service, calendar_service = authenticate_google_services()

        # 🌦️ Fetch weather data
        weather_data = get_weather()
        hot_weather = weather_analysis(weather_data)

        # 📝 Fetch and parse tasks
        parsed_tasks = parse_personal_and_work_tasks()
        if not parsed_tasks:
            raise ValueError("No tasks found. Ensure tasks are correctly labeled and accessible.")

        print(f"Parsed {len(parsed_tasks)} tasks.")

        # 🕰️ Fetch working hours and energy levels
        energy_profile = fetch_working_hours_and_energy_levels(sheets_service, weather_analysis=hot_weather)
        if not energy_profile:
            raise ValueError("No working hours or energy levels found in Google Sheets.")

        # 📅 Fetch existing events and occupied slots
        meetings, tasks, travel_times, screen_free_times, occupied_slots = fetch_calendar_events(calendar_service)

        if not meetings and not travel_times and not screen_free_times:
            print("No upcoming events found.")

        # 🔄 Merge overlapping occupied slots
        occupied_slots = merge_overlapping_intervals(occupied_slots)

        # 🔢 Calculate task scores and sort by priority
        scored_tasks = [(task, calculate_task_score(task)) for task in parsed_tasks]
        scored_tasks.sort(key=lambda x: x[1], reverse=True)

        print("\n🏆 Prioritized Tasks:")
        for task, score in scored_tasks:
            print(f"{task['name']}, Score: {score:.2f}")

        # 📆 Generate available timeslots
        available_timeslots = {}
        for task, _ in scored_tasks:
            task_id = task["id"]
            timeslots = get_available_timeslots(
                energy_profile,
                occupied_slots,
                task["task_type"],
                task["energy_level"],
                task["deadline"],
            )
            if timeslots:
                available_timeslots[task_id] = timeslots

        # 📌 Schedule tasks
        log_message("INFO", "Scheduling Tasks")
        scheduled_tasks = schedule_tasks(
            [task for task, _ in scored_tasks],
            available_timeslots,
            occupied_slots,
            tasks
        )

        # 🔄 Merge consecutive scheduled tasks
        merged_scheduled_tasks = merge_scheduled_tasks(scheduled_tasks)

        # 📌 Store scheduled tasks in dictionary format for tracking
        scheduled_task_dict = {}
        for task in merged_scheduled_tasks:
            task_id = task["task_id"]
            if task_id not in scheduled_task_dict:
                scheduled_task_dict[task_id] = []
            scheduled_task_dict[task_id].append({
                "start_time": task["start_time"],
                "end_time": task["end_time"],
                "event_id": task.get("event_id", None)  # Track event ID if it exists
            })

        # 🔍 Debugging: Print scheduled task dictionary
        print("\n📌 Scheduled Task Dictionary:")
        for task_id, segments in scheduled_task_dict.items():
            print(f"Task ID: {task_id}")
            for event in segments:
                print(f"  Event ID: {event['event_id']} | {event['start_time']} → {event['end_time']}")

        # 📅 Manage Google Calendar events
        existing_tasks_grouped = {task["id"]: task for task in tasks}  # Ensure it's a dictionary
        for task_id, scheduled_segments in scheduled_task_dict.items():
            print(f"DEBUG: scheduled_task_dict → {scheduled_task_dict}")

            if not scheduled_segments:
                print(f"⚠️ No scheduled segments found for Task ID {task_id}. Skipping.")
                continue  # Skip this task

            # Fetch the task name safely for logging
            task_name = next(
                (task["name"] for task in parsed_tasks if task["id"] == task_id), 
                f"Task {task_id}"
            )

            # Check existing events for this task
            if task_id in existing_tasks_grouped:
                existing_events = existing_tasks_grouped[task_id]
                existing_starts = {parse_event_datetime(e)[0] for e in existing_events}

                # Update only if necessary
                for segment in scheduled_segments:
                    if segment["start_time"] not in existing_starts:
                        print(f"Updating event for task '{task_name}'...")
                        manage_calendar_events(calendar_service, [segment], existing_tasks_grouped)
            else:
                print(f"Creating new event for task '{task_name}'...")
                manage_calendar_events(calendar_service, scheduled_segments, existing_tasks_grouped)

        # ✅ Execution Complete
        code_end = time.time()
        print(f"\n✅ Scheduling took {code_end - code_start:.2f} seconds to complete")

    except Exception as e:
        print(f"❌ An error occurred during execution: {e}")