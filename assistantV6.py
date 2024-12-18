import logging
import os
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from todoist_api_python.api import TodoistAPI
from datetime import datetime, timedelta, timezone
from dateutil import parser # Import robust ISO 8601 parser
from dateutil.parser import parse as dateutil_parse
import pytz
import re
import requests
import traceback

# --- Setup ---
# Logging
logging.basicConfig(
    filename="task_scheduler.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

def log_message(level, message):
    if level == "INFO":
        logging.info(message)
    elif level == "ERROR":
        logging.error(message)
    elif level == "WARNING":
        logging.warning(message)
    print(message)  # Immediate feedback

# Load API keys - .env added to gitignore so these will not accidentally be uploaded to GitHub. 
load_dotenv(dotenv_path="API_keys.env")

#Refresh OAuth2 token if it has expired. 
def refresh_token_if_needed():
    """Checks if the token is expired and refreshes it if needed."""
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

# -- Get weather from Met Office DataHub API
# Pull api_key from .env file
api_key = os.getenv("API_KEY")
if not api_key:
    raise ValueError("API key is not set. Please check your environment variables.")

#Define location for where to call the API for, as latitude and longitude - currently set Grimsby, North East Lincolnshire. To change, pull lat and long. from Google Maps
latitude = '53.56685606194195'
longitude = '-0.08339315195789283'

# Define the base URL for the Met Office DataHub API
base_url = 'https://data.hub.api.metoffice.gov.uk/sitespecific/v0'

# Specify the endpoint for the type of weather data you need (e.g., hourly forecast)
endpoint = '/point/hourly'

# Build the full URL includeLocationName=TRUE means json response also contains the name of the weather station it is pulling data from. 
# If recently changed, also call debug print full response to confirm identity of weather station
url = f"{base_url}{endpoint}?includeLocationName=TRUE&latitude={latitude}&longitude={longitude}"

# Set up headers for the request
headers = {
    'Accept': 'application/json',
    'apikey': api_key,
    'User-Agent': 'Python/requests'
}

#Pull next 48 hours weather data from Met Office, keep time and "feelsLikeTemperature"
def get_weather(): 
    # List to store weather data
    weather_data = []
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the JSON response
        data = response.json()

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
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
    except requests.exceptions.RequestException as err:
        print(f"Other error occurred: {err}")
    return weather_data

#search weather_data to confirm temperature is/isn't >22c in the next 48 hours.
def weather_analysis(weather_data, threshold_temp=22):
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
    try:
        # Fetch all tasks
        tasks = api.get_tasks()
        parsed_tasks = []

        def extract_estimated_time(description, default_time=60):
            """
            Extracts estimated time from task description or notes.
            Matches both compact (e.g., '1h', '30m') and natural language (e.g., '1 hour').
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
            """
            if not task_due:
                return None  # Handle missing due field gracefully

            # If the task has a datetime (timezone-aware or naive)
            if hasattr(task_due, 'datetime') and task_due.datetime:
                # Parse datetime and make it timezone-aware if it's naive
                deadline = parse_datetime(task_due.datetime)
                return deadline

            # If the task has only a date (whole-day task)
            if hasattr(task_due, 'date') and task_due.date:
                # If the date is a string, convert it to a datetime object
                if isinstance(task_due.date, str):
                    task_date = datetime.strptime(task_due.date, '%Y-%m-%d')  # Adjust format if necessary
                else:
                    task_date = task_due.date

                # Make sure the datetime is timezone-aware
                if task_date.tzinfo is None:
                    task_date = pytz.utc.localize(task_date)  # Localize to UTC if it's naive

                # Set the deadline to 23:59:59 on the due date
                task_end_of_day = task_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                return task_end_of_day

            return None  # Return None if neither field is available

        def parse_datetime(date_string):
            """
            Converts a datetime string into a timezone-aware datetime object.
            Assumes the date string is in UTC if no timezone information is present.
            """
            try:
                # Try parsing as a datetime with timezone info
                dt = datetime.fromisoformat(date_string)
                if dt.tzinfo is None:
                    # If the datetime is naive, make it UTC by default
                    dt = pytz.utc.localize(dt)
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
    Fetches and generates an energy profile for the next 28 days.
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
                        start_time = datetime.strptime(f"{current_date.strftime('%Y-%m-%d')} {start_time_str}", "%Y-%m-%d %H:%M")
                        end_time = datetime.strptime(f"{current_date.strftime('%Y-%m-%d')} {end_time_str}", "%Y-%m-%d %H:%M")
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
# Fetch events from Google Calendar - will not pull programmatically created tasks
def fetch_calendar_events(calendar_service, time_min=None, time_max=None):
    if not time_min:
        time_min = datetime.utcnow().isoformat() + 'Z'
    if not time_max:
        time_max = (datetime.utcnow() + timedelta(days=28)).isoformat() + 'Z'

    # Paginate through all events
    events = []
    page_token = None
    while True:
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime',
            pageToken=page_token
        ).execute()

        # Extend the events list with the current page
        page_events = events_result.get('items', [])
        # Filter out programmatic events by default
        page_events = [
            event for event in page_events
            if 'Programmatic: true' not in event.get('description', '')
        ]
        events.extend(page_events)

        # Handle pagination
        page_token = events_result.get('nextPageToken')
        if not page_token:
            break

    return events

def ensure_datetime(value):
    """Ensure a value is a datetime object and make it offset-aware."""
    if isinstance(value, str):
        try:
            # Parse ISO 8601 strings with dateutil.parser
            dt = parser.isoparse(value)
            # Ensure the datetime is offset-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)  # Assume UTC for naive datetimes
            return dt
        except ValueError:
            raise ValueError(f"Invalid datetime string: {value}")
    elif isinstance(value, datetime):
        # Make sure datetime is offset-aware
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)  # Assume UTC for naive datetimes
        return value
    else:
        raise TypeError(f"Expected datetime object or string, got {type(value).__name__}")
    
def parse_event_datetime(event):
    """Parse start and end times from a calendar event and ensure they are datetime objects."""
    try:
        start_time = event['start'].get('dateTime') or event['start'].get('date')
        end_time = event['end'].get('dateTime') or event['end'].get('date')
        return ensure_datetime(start_time), ensure_datetime(end_time)
    except Exception as e:
        print(f"An error occurred while parsing event times: {e}")
        return None, None
        
# Adds travel events before and after a meeting if it has a location
def add_travel_event(calendar_service, task_name, travel_start, travel_end, location):
    try:
        # Add travel before the meeting
        event_before = {
            'summary': f'Travel to {task_name}',
            'description': f'Travel time to {location}',
            'start': {
                'dateTime': travel_start.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': travel_end.isoformat(),
                'timeZone': 'UTC',
            },
        }
        calendar_service.events().insert(calendarId='primary', body=event_before).execute()

        # Add travel after the meeting
        event_after = {
            'summary': f'Travel from {task_name}',
            'description': f'Travel time from {location}',
            'start': {
                'dateTime': travel_end.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': (travel_end + timedelta(minutes=30)).isoformat(),
                'timeZone': 'UTC',
            },
        }
        calendar_service.events().insert(calendarId='primary', body=event_after).execute()

        print(f"Travel events added for task: {task_name}")
    except Exception as e:
        print(f"Failed to add travel events for task {task_name}: {e}")

# Is this a virtual meeting depending on description or location, with case insensitive matching
def is_virtual_meeting(event):
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

# Add a 15-minute rest period after a virtual meeting as a separate Google Calendar event.
def add_rest_period(calendar_service, end_time):
    rest_start_time = end_time
    rest_end_time = rest_start_time + timedelta(minutes=15)

    # Format the times in the ISO 8601 format, which is required by the Google Calendar API
    rest_start_time_str = rest_start_time.isoformat()
    rest_end_time_str = rest_end_time.isoformat()

    # Create the event details
    event = {
        'summary': 'Screen-Free Time',
        'description': 'Take a short break after the virtual meeting.',
        'start': {
            'dateTime': rest_start_time_str,
            'timeZone': 'UTC',  # You can adjust the timezone as per your location
        },
        'end': {
            'dateTime': rest_end_time_str,
            'timeZone': 'UTC',  # Adjust the timezone as necessary
        },
    }

    #try:
        # Insert the event into the Google Calendar
        #event_result = calendar_service.events().insert(
            #calendarId='primary',  # Insert into the primary calendar
           #body=event
        #).execute()

        # Log that the event was created successfully
        #log_message("INFO", f"Rest period added to Google Calendar: {event_result['summary']} from {rest_start_time_str} to {rest_end_time_str}")
    #except Exception as e:
        #log_message("ERROR", f"Failed to add rest period to Google Calendar: {e}")

# Handle meeting with location and add travel time and rest period if virtual
def handle_meeting_with_location(calendar_service, event, location=None, travel_time=None):
    """Add travel time and rest period after meeting if virtual."""
    # Handle both 'dateTime' and 'date' keys
    start_time_str = event['start'].get('dateTime', event['start'].get('date'))
    end_time_str = event['end'].get('dateTime', event['end'].get('date'))

    # Parse the start and end times
    start_time = parser.isoparse(start_time_str)
    end_time = parser.isoparse(end_time_str)

    if location:
        # Add travel time
        travel_duration = travel_time or 30  # Default to 30 minutes
        travel_start = start_time - timedelta(minutes=travel_duration)
        travel_end = start_time

        # Add travel events in Google Calendar
        add_travel_event(calendar_service, event['summary'], travel_start, travel_end, location)

    # If it's a virtual meeting, add rest period
    if is_virtual_meeting(event):
        add_rest_period(calendar_service, end_time)

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
maximum_energy = 3  # Energy level on a scale of 1 to 3

# Function to calculate task score
def calculate_task_score(task):
    # Calculate the task score (as in your original function)
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

        # Ensure both deadline and current time are timezone-aware or both naive
        if deadline.tzinfo is None:  # Naive deadline, make it aware
            deadline = pytz.utc.localize(deadline)
        if datetime.now().tzinfo is None:  # Naive current time, make it aware
            current_time = datetime.now(pytz.utc)
        else:  # Use current time with timezone if it's already aware
            current_time = datetime.now()

        # Calculate days difference between deadline and current time
        deadline_days = (deadline - current_time).days

    # Normalize scores
    time_score = (time_needed / max_time) * weights["T"]
    energy_score = (energy_required / maximum_energy) * weights["E"]
    impact_score = impact * weights["I"]
        # Calculate the deadline score
    if deadline_days >= 0:
        deadline_score = (1 / (deadline_days + 1)) * weights["D"]  # Task is not overdue
    else:
        deadline_score = 2  # Task is overdue, fixed value for high urgency

    # Composite score
    score = time_score + energy_score + impact_score + deadline_score
    return score

def convert_energy_level_to_int(energy_level):
    """Converts energy level from string to integer for comparison."""
    energy_mapping = {'low': 1, 'medium': 2, 'high': 3}
    return energy_mapping.get(energy_level.lower(), 0)

def get_available_timeslots(energy_profile, calendar_events, task_type, task_energy_level, task_deadline):
    """
    Determines available timeslots by comparing energy profile and occupied slots from the calendar,
    and filters them based on the task's energy level, deadline, and current time.
    Handles partially available slots and updates the available slots accordingly.
    """
    # Ensure the task deadline is offset-aware (e.g., UTC)
    if task_deadline and task_deadline.tzinfo is None:
        task_deadline = pytz.utc.localize(task_deadline)

    # Get the current time in UTC and make it offset-aware
    current_time = datetime.utcnow().replace(microsecond=0, tzinfo=pytz.utc)

    # Gather occupied slots from calendar events and make them offset-aware
    occupied_slots = []
    for event in calendar_events:
        start_time, end_time = parse_event_datetime(event)
        if start_time and end_time:
            if start_time.tzinfo is None:
                start_time = pytz.utc.localize(start_time)
            if end_time.tzinfo is None:
                end_time = pytz.utc.localize(end_time)
            occupied_slots.append((start_time, end_time))

    available_timeslots = {}

    for day, slots in energy_profile.items():
        available_slots = []  # Initialize available_slots for each day
        
        for slot in slots:
            slot_start, slot_end = slot["time_range"]
            slot_start = ensure_datetime(slot_start)
            slot_end = ensure_datetime(slot_end)

            # Make slot times offset-aware (e.g., UTC)
            if slot_start.tzinfo is None:
                slot_start = pytz.utc.localize(slot_start)
            if slot_end.tzinfo is None:
                slot_end = pytz.utc.localize(slot_end)

            # Skip slots that are in the past (start before the current time) or after the task's deadline
            if slot_start < current_time or slot_end > task_deadline:
                continue

            # Convert energy levels to integers for comparison
            slot_energy_level = convert_energy_level_to_int(slot["energy_level"])
            task_energy_level_int = convert_energy_level_to_int(task_energy_level)

            # Check if slot matches task type and energy level
            if slot_energy_level >= task_energy_level_int and slot["task_type"] == task_type:
                slot_duration = slot_end - slot_start

                # If the slot is fully available and large enough, use it directly
                if slot_duration >= timedelta(minutes=task["estimated_time"]):
                    available_slots.append((slot_start, slot_end))
                else:
                    # If the slot is partially available, break it into 15-minute chunks
                    current_chunk_start = slot_start
                    chunks = []  # List to store chunks
                    while current_chunk_start < slot_end:
                        current_chunk_end = min(current_chunk_start + timedelta(minutes=15), slot_end)

                        # Check if the chunk overlaps with any occupied slots
                        is_available = True
                        for occupied_start, occupied_end in occupied_slots:
                            if current_chunk_start < occupied_end and current_chunk_end > occupied_start:
                                is_available = False
                                break

                        if is_available:
                            chunks.append((current_chunk_start, current_chunk_end))

                        # Move to the next chunk
                        current_chunk_start = current_chunk_end

                    # If there are chunks, merge them into one larger slot
                    if chunks:
                        merged_start = chunks[0][0]
                        merged_end = chunks[-1][1]
                        available_slots.append((merged_start, merged_end))

        if available_slots:
            available_timeslots[day] = available_slots

    return available_timeslots

def print_suitable_timeslots(energy_profile, calendar_events, task_type, task_energy_level, task_deadline):
    suitable_timeslots = get_available_timeslots(energy_profile, calendar_events, task_type, task_energy_level, task_deadline)
    
    print("Suitable timeslots for the task:")
    for day, slots in suitable_timeslots.items():
        print(f"Day: {day}")
        for start_time, end_time in slots:
            print(f"  From {start_time} to {end_time}")

# -- Task scheduling logic. Using Greedy Algorithm
def schedule_tasks(tasks, available_timeslots, occupied_slots):
    """
    Schedules tasks using a greedy approach, ensuring tasks are scheduled into available timeslots
    without overlap with occupied slots.
    """
    scheduled_tasks = []

    for task in tasks:
        task_name = task['name']
        task_duration = timedelta(minutes=task['estimated_time'])
        remaining_duration = task_duration

        if task_name not in available_timeslots:
            print(f"No available timeslots for task: {task_name}")
            continue

        # Fetch the available timeslots for this task
        task_timeslots = available_timeslots[task_name]

        for day, slots in sorted(task_timeslots.items()):
            updated_slots = []  # To store updated slots after allocation

            # Iterate over each slot for this day
            for slot in slots[:]:  # Use a copy of slots to safely modify the original list
                try:
                    if isinstance(slot, tuple) and len(slot) == 2:
                        slot_start, slot_end = slot  # Unpack the tuple
                    else:
                        print(f"Unexpected slot format: {slot}")  # Log the slot that caused the error
                        continue  # Skip this slot if it doesn't match the expected format
                except Exception as e:
                    print(f"Error unpacking slot: {slot}. Error: {e}")
                    continue  # Skip problematic slots

                # Check for overlap with occupied slots
                is_available = True
                for occupied_start, occupied_end in occupied_slots:
                    if slot_start < occupied_end and slot_end > occupied_start:
                        is_available = False
                        break

                if not is_available:
                    continue  # Skip this slot if it overlaps with any occupied slot

                # Calculate the duration of the current slot
                slot_duration = slot_end - slot_start
                if remaining_duration <= timedelta(0):
                    break  # Task is fully scheduled

                # Allocate the task duration from the current slot
                allocated_duration = min(slot_duration, remaining_duration)
                scheduled_tasks.append({
                    "task_name": task_name,
                    "start_time": slot_start,
                    "end_time": slot_start + allocated_duration,
                    "day": day,
                })
                #print(f"      Allocated {allocated_duration} from {slot_start} to {slot_start + allocated_duration}")

                # Update the remaining slot time
                leftover_start = slot_start + allocated_duration
                if leftover_start < slot_end:
                    # The slot is partially used, add the remaining part
                    updated_slots.append((leftover_start, slot_end))

                # Update the remaining task duration
                remaining_duration -= allocated_duration
                #print(f"      Remaining duration: {remaining_duration}")

                # Add this allocated time to occupied slots to prevent future overlaps
                occupied_slots.append((slot_start, slot_start + allocated_duration))

            # Update the available slots for this day with the remaining available slots
            available_timeslots[task_name][day] = updated_slots

            if remaining_duration <= timedelta(0):
                break  # Task fully scheduled, exit the day loop

        if remaining_duration > timedelta(0):
            print(f"Unable to fully schedule task: {task_name}. Remaining: {remaining_duration}")
            #LOGGING

    return scheduled_tasks

def merge_scheduled_tasks(scheduled_tasks):
    """
    Merges consecutive scheduled tasks into larger slots if they belong to the same task and day.

    Args:
        scheduled_tasks (list): List of scheduled tasks with their allocated times.

    Returns:
        list: Optimized list of scheduled tasks with consecutive slots merged.
    """
    if not scheduled_tasks:
        return []

    # Sort tasks by task_name, day, and start_time for proper merging
    scheduled_tasks.sort(key=lambda x: (x["task_name"], x["day"], x["start_time"]))

    merged_tasks = []
    current_task = scheduled_tasks[0]

    for next_task in scheduled_tasks[1:]:
        # Check if the next task is consecutive and belongs to the same task and day
        if (
            next_task["task_name"] == current_task["task_name"]
            and next_task["day"] == current_task["day"]
            and next_task["start_time"] == current_task["end_time"]
        ):
            # Extend the current task's end_time
            current_task["end_time"] = next_task["end_time"]
        else:
            # Add the current task to merged_tasks and start a new current_task
            merged_tasks.append(current_task)
            current_task = next_task

    # Add the last task
    merged_tasks.append(current_task)

    return merged_tasks

def merge_overlapping_intervals(intervals):
    """
    Merge overlapping or adjacent intervals in a list of time ranges.
    Args:
        intervals (list): List of tuples (start_time, end_time).
    Returns:
        list: Merged intervals.
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

# -- Colour settings for Google Calendar tasks
#colour Name	ID
#Lavender	1
#Sage	2
#Grape	3
#Flamingo	4
#Banana	5
#Tangerine	6
#Peacock	7
#Graphite	8
#Blueberry	9
#Basil	10
#Tomato	11

COLOuR_MAPPING = {

    "emails": "3",  
    "admin": "9",  
    "writing": "7",  
    "data analysis": "10",
    "thinking_planning": "2",
    "reading_searching": "6",
    "giving_talks": "4",
}

def schedule_event(calendar_service, task_name, start_time, end_time, labels):
    """
    Schedule an event in Google Calendar with a specific color based on task labels.

    Args:
        calendar_service: Google Calendar service instance.
        task_name (str): Task name.
        start_time (datetime): Start time of the event.
        end_time (datetime): End time of the event.
        labels (list): List of labels associated with the task.
    """
    # Determine the colourId based on task labels
    task_colour = None
    for label in labels:
        if label in COLOuR_MAPPING:
            task_colour = COLOuR_MAPPING[label]
            break  # Use the first matching color

    # Build the event body
    event = {
        'summary': task_name,
        'description': 'Scheduled by task scheduler',
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'UTC',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'UTC',
        },
        'colorId': task_colour,  # Set the event color
    }

    try:
        calendar_service.events().insert(calendarId='primary', body=event).execute()
        print(f"Scheduled: {task_name} from {start_time} to {end_time}")
    except Exception as e:
        print(f"Failed to schedule task '{task_name}': {e}")

        
if __name__ == "__main__":
    try:
        # Refresh token if needed
        creds = refresh_token_if_needed()
        if creds:
            log_message("INFO", f"Token refreshed successfully.")
        else:
            print("Authentication required!")

        # Authenticate services
        sheets_service, calendar_service = authenticate_google_services()

        # Fetch weather data and determine if it's hot weather for adjustments in energy levels
        weather_data = get_weather()
        hot_weather = weather_analysis(weather_data)

        # Fetch and parse tasks
        parsed_tasks = parse_personal_and_work_tasks()
        if not parsed_tasks:
            raise ValueError("No tasks found. Ensure tasks are correctly labeled and accessible.")

        print(f"Parsed {len(parsed_tasks)} tasks.")

        # Fetch working hours and energy levels
        energy_profile = fetch_working_hours_and_energy_levels(sheets_service, weather_analysis=hot_weather)
        if not energy_profile:
            raise ValueError("No working hours or energy levels found in Google Sheets.")

        # Fetch calendar events
        calendar_events = fetch_calendar_events(calendar_service)
        if not calendar_events:
            print("No upcoming events found.")
        else:
            for event in calendar_events:
                location = event.get('location', None)
                handle_meeting_with_location(calendar_service, event, location)

        # Print occupied slots from the calendar events with travel and rest times
        print("\nOccupied Slots (with Travel and Rest Time):")
        occupied_slots = []  # List to hold the occupied slots with travel/rest times
        for event in calendar_events:
            start_time, end_time = parse_event_datetime(event)
            if start_time and end_time:
                if start_time.tzinfo is None:
                    start_time = pytz.utc.localize(start_time)
                if end_time.tzinfo is None:
                    end_time = pytz.utc.localize(end_time)

                event_name = event.get("summary", "No Name")
                print(f"Occupied Slot: {event_name} from {start_time} to {end_time}")
                occupied_slots.append((start_time, end_time))

        # Merge overlapping occupied slots
        occupied_slots = merge_overlapping_intervals(occupied_slots)

        # Calculate task scores and sort by priority
        scored_tasks = [(task, calculate_task_score(task)) for task in parsed_tasks]
        scored_tasks = sorted(scored_tasks, key=lambda x: x[1], reverse=True)
        print("\nPrioritized Tasks:")
        for task, score in scored_tasks:
            print(f"Task: {task['name']}, Score: {score:.2f}")

        # Generate and collect available time slots for each task
        available_timeslots = {}
        for task, _ in scored_tasks:
            task_type = task["task_type"]
            task_energy_level = task["energy_level"]
            task_deadline = task["deadline"]

            timeslots = get_available_timeslots(energy_profile, calendar_events, task_type, task_energy_level, task_deadline)
            available_timeslots[task['name']] = timeslots

        # Schedule tasks using the greedy algorithm
        log_message("INFO", f"Scheduling Tasks")
        scheduled_tasks = schedule_tasks([task for task, _ in scored_tasks], available_timeslots, occupied_slots)

        # Merge consecutive scheduled tasks
        merged_scheduled_tasks = merge_scheduled_tasks(scheduled_tasks)

        # Display the merged scheduled tasks
        print("\nScheduled Tasks:")
        for task in merged_scheduled_tasks:
            log_message("INFO", f"Task '{task['task_name']}' scheduled from {task['start_time']} to {task['end_time']}")

        # Add merged scheduled tasks to Google Calendar
        for scheduled_task in merged_scheduled_tasks:
            task_name = scheduled_task["task_name"]
            start_time = scheduled_task["start_time"]
            end_time = scheduled_task["end_time"]
            
            # Fetch task labels for determining the color
            labels = next(
                (task["labels"] for task in parsed_tasks if task["name"] == task_name),
                []
            )
            schedule_event(calendar_service, task_name, start_time, end_time, labels)

        print("\nScheduling Complete.")
        
    except Exception as e:
        print(f"An error occurred during execution: {e}")
