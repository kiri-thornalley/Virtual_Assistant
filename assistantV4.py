from todoist_api_python.api import TodoistAPI
from datetime import datetime, timedelta, timezone
from dateutil import parser # Import robust ISO 8601 parser
from dateutil.parser import parse as dateutil_parse
import pytz
import re
import requests
import os
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

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

        ## Debug print to inspect the full response structure##
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

            ## DEBUG Print the stored data
            #print("\nStored Weather Data:")
            #for entry in weather_data:
            #    print(entry)

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
               # print(f"Time: {entry['time']} - Feels like temperature is {entry['feelsLikeTemperature']}°C")
        else:
            print(f"Time: {entry['time']} - Feels like temperature data not available.")
    return False  # Return False if no entry exceeds the threshold

# -- Get Data - Tasks from Todoist, Energy levels and working hours from Google Sheets
api_key = os.getenv("TODOIST_API_KEY")
if not api_key:
    raise ValueError("TODOIST_API_KEY not found in environment variables. Please check your .env file.")

# Initialize the Todoist API with the key from the .env file
api = TodoistAPI(api_key)

def parse_datetime(datetime_str):
    """
    Parses a datetime string and returns a datetime object.
    Supports ISO 8601 format and standard datetime formats.
    """
    try:
        # Attempt ISO 8601 parsing with optional 'Z' handling for UTC
        return datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
    except ValueError:
        # Fall back to dateutil for other formats
        try:
            return dateutil_parse(datetime_str)
        except Exception as e:
            print(f"Failed to parse datetime: {datetime_str}. Error: {e}")
            return None
        

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
            Parses the due date or datetime for a task.
            """
            if not task_due:
                return None  # Handle missing due field gracefully

            # Access properties directly
            if hasattr(task_due, 'datetime') and task_due.datetime:
                return parse_datetime(task_due.datetime)

            if hasattr(task_due, 'date') and task_due.date:
                return parse_datetime(task_due.date)

            return None  # Return None if neither field is available

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
                elif 4 <= energy_level <= 7:
                    return "medium"
                elif 8 <= energy_level <= 10:
                    return "high"
                return "unknown"
            except ValueError:
                print(f"Invalid energy level value: {energy_level}")
                return "unknown"

        # Get today's date
        today = datetime.today()

        for row in rows:
            if len(row) >= 4:
                day_name = row[0]  # Expecting the day name (e.g., "Monday")
                time_range = row[1]
                task_type = row[2]

                # Debugging: Print out the content of day_name
                #print(f"Processing row: {row}")
                #print(f"Day name: '{day_name}'")

                # Validate that the day name is correct
                if day_name not in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
                    print(f"Skipping row with invalid day name: {day_name}")
                    continue

                # Calculate the date for the given day name in the upcoming week
                day_index = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(day_name)
                days_ahead = (day_index - today.weekday() + 7) % 7  # Days until the next occurrence of the day
                next_day_date = today + timedelta(days=days_ahead)

                # Handle the time range parsing
                if not time_range or " - " not in time_range:
                    print(f"Skipping row with invalid time range format: {row}")
                    continue

                start_time_str, end_time_str = time_range.split(' - ')

                # Construct datetime objects by combining the calculated date and time strings
                try:
                    start_time = datetime.strptime(f"{next_day_date.strftime('%Y-%m-%d')} {start_time_str}", "%Y-%m-%d %H:%M")
                    end_time = datetime.strptime(f"{next_day_date.strftime('%Y-%m-%d')} {end_time_str}", "%Y-%m-%d %H:%M")
                except ValueError as e:
                    print(f"Skipping row due to invalid time format: {time_range} ({e})")
                    continue

                # Handle overnight slots (e.g., "23:00 - 00:00")
                if end_time <= start_time:
                    first_part = (start_time, datetime.strptime(f"{start_time.date()} 23:59", "%Y-%m-%d %H:%M"))
                    next_day = start_time + timedelta(days=1)
                    second_part = (datetime.strptime(f"{next_day.date()} 00:00", "%Y-%m-%d %H:%M"), end_time)

                    if day_name not in energy_profile:
                        energy_profile[day_name] = []

                    energy_profile[day_name].append({
                        "time_range": first_part,
                        "task_type": task_type,
                        "energy_level": convert_energy_level(row[3])
                    })

                    next_day_name = next_day.strftime("%A")
                    if next_day_name not in energy_profile:
                        energy_profile[next_day_name] = []

                    energy_profile[next_day_name].append({
                        "time_range": second_part,
                        "task_type": task_type,
                        "energy_level": convert_energy_level(row[3])
                    })

                    continue  # Skip the current iteration since this overnight slot is handled

                # Add the time range to the energy profile for the specified day
                if day_name not in energy_profile:
                    energy_profile[day_name] = []

                energy_profile[day_name].append({
                    "time_range": (start_time, end_time),
                    "task_type": task_type,
                    "energy_level": convert_energy_level(row[3])
                })

        if not energy_profile:
            print("No valid working hours or energy levels found in Google Sheets.")
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

    try:
        # Insert the event into the Google Calendar
        event_result = calendar_service.events().insert(
            calendarId='primary',  # Insert into the primary calendar
            body=event
        ).execute()

        # Log that the event was created successfully
        log_message("INFO", f"Rest period added to Google Calendar: {event_result['summary']} from {rest_start_time_str} to {rest_end_time_str}")
    except Exception as e:
        log_message("ERROR", f"Failed to add rest period to Google Calendar: {e}")

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
available_energy = 3  # Energy level on a scale of 1 to 3


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
        deadline_days = (deadline - datetime.now()).days

    # Normalize scores
    time_score = (time_needed / max_time) * weights["T"]
    energy_score = (energy_required / available_energy) * weights["E"]
    impact_score = impact * weights["I"]
    deadline_score = (1 / (deadline_days + 1)) * weights["D"] if deadline_days >= 0 else 2

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

    # Function to split a given time range into 15-minute chunks
    def split_time_range_into_chunks(start_time, end_time, chunk_duration=timedelta(minutes=15)):
        chunks = []
        current_time = start_time
        while current_time + chunk_duration <= end_time:
            chunk_end_time = current_time + chunk_duration
            chunks.append((current_time, chunk_end_time))
            current_time = chunk_end_time
        return chunks

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

            # Skip slots that are in the past (start before the current time)
            if slot_start < current_time:
                continue

            # Convert energy levels to integers for comparison
            slot_energy_level = convert_energy_level_to_int(slot["energy_level"])
            task_energy_level_int = convert_energy_level_to_int(task_energy_level)

            # Check if slot matches task type and energy level
            if slot_energy_level >= task_energy_level_int and slot["task_type"] == task_type:
                # Check if the slot is fully available or partially occupied
                is_fully_available = True
                for occupied_start, occupied_end in occupied_slots:
                    if slot_start < occupied_end and slot_end > occupied_start:
                        is_fully_available = False
                        break

                if is_fully_available:
                    # If fully available, add the entire slot
                    available_slots.append((slot_start, slot_end))
                else:
                    # If partially occupied, split the slot into 15-minute chunks
                    chunks = split_time_range_into_chunks(slot_start, slot_end)
                    for chunk_start, chunk_end in chunks:
                        is_chunk_available = True
                        for occupied_start, occupied_end in occupied_slots:
                            if chunk_start < occupied_end and chunk_end > occupied_start:
                                is_chunk_available = False
                                break

                        # Check if the chunk is within the task's deadline and not past current time
                        if is_chunk_available and chunk_end <= task_deadline and chunk_start >= current_time:
                            available_slots.append((chunk_start, chunk_end))

        if available_slots:
            available_timeslots[day] = available_slots

    return available_timeslots

# -- Task scheduling logic. Using Greedy Algorithm

def schedule_tasks(scored_tasks, available_timeslots, calendar_service):
    """
    Schedule tasks using a greedy approach.
    Args:
        scored_tasks (list): List of tasks sorted by priority (task, score).
        available_timeslots (dict): Available time slots by day.
        calendar_service: Google Calendar service instance.
    """
    for task, _ in scored_tasks:
        task_name = task['name']
        task_duration = timedelta(minutes=task['estimated_time'])
        task_deadline = task['deadline']
        task_energy_level = convert_energy_level_to_int(task['energy_level'])

        scheduled = False

        for day, slots in available_timeslots.items():
            if scheduled:
                break

            for i, (slot_start, slot_end) in enumerate(slots):
                slot_duration = slot_end - slot_start

                # If the slot can fully accommodate the task
                if slot_duration >= task_duration:
                    # Schedule the task
                    schedule_event(calendar_service, task_name, slot_start, slot_start + task_duration)
                    # Update available slots
                    slots.pop(i)
                    scheduled = True
                    break

                # If the task needs more time, try merging consecutive slots
                elif slot_duration < task_duration:
                    remaining_duration = task_duration - slot_duration
                    merged_end = slot_end

                    for j in range(i + 1, len(slots)):
                        next_slot_start, next_slot_end = slots[j]

                        # Check if slots are consecutive
                        if next_slot_start <= merged_end:
                            merge_duration = next_slot_end - next_slot_start
                            merged_end = next_slot_end
                            remaining_duration -= merge_duration

                            if remaining_duration <= timedelta(0):
                                # Schedule the task across merged slots
                                schedule_event(calendar_service, task_name, slot_start, slot_start + task_duration)
                                # Remove merged slots
                                slots = slots[:i] + slots[j + 1:]
                                available_timeslots[day] = slots
                                scheduled = True
                                break
                        else:
                            break

                if scheduled:
                    break

        if not scheduled:
            print(f"Could not fully schedule task '{task_name}'. Trying to split across days.")
            remaining_duration = task_duration

            for day, slots in available_timeslots.items():
                for i, (slot_start, slot_end) in enumerate(slots):
                    slot_duration = slot_end - slot_start

                    if remaining_duration <= timedelta(0):
                        break

                    chunk_duration = min(slot_duration, remaining_duration)
                    schedule_event(calendar_service, task_name, slot_start, slot_start + chunk_duration)
                    remaining_duration -= chunk_duration

                    # Update available slots
                    if chunk_duration == slot_duration:
                        slots.pop(i)
                    else:
                        slots[i] = (slot_start + chunk_duration, slot_end)

                if remaining_duration <= timedelta(0):
                    break

            if remaining_duration > timedelta(0):
                print(f"Task '{task_name}' could not be fully scheduled.")

def schedule_event(calendar_service, task_name, start_time, end_time):
    """
    Schedule an event in Google Calendar.
    Args:
        calendar_service: Google Calendar service instance.
        task_name (str): Task name.
        start_time (datetime): Start time of the event.
        end_time (datetime): End time of the event.
    """
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
    }
    try:
        calendar_service.events().insert(calendarId='primary', body=event).execute()
        print(f"Scheduled: {task_name} from {start_time} to {end_time}")
    except Exception as e:
        print(f"Failed to schedule task '{task_name}': {e}")


#Also floating tasks like Lunch, instead of splitting, schedule shorter block, not less than 30 mins. 


if __name__ == "__main__":
    try:
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

        # Calculate task scores and sort by priority
        scored_tasks = [(task, calculate_task_score(task)) for task in parsed_tasks]
        scored_tasks = sorted(scored_tasks, key=lambda x: x[1], reverse=True)

        print("Prioritized Tasks:")
        for task, score in scored_tasks:
            print(f"Task: {task['name']}, Score: {score:.1f}")

        # Generate available time slots for each task
        for task, _ in scored_tasks:
            task_type = task["task_type"]
            task_energy_level = task["energy_level"]
            task_deadline = task["deadline"]

        available_timeslots = get_available_timeslots(energy_profile, calendar_events, task_type, task_energy_level, task_deadline)

        # Schedule tasks
        schedule_tasks(scored_tasks, available_timeslots, calendar_service)

    except Exception as e:
        print(f"An error occurred during execution: {e}")

