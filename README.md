# Virtual Assistant for Productivity and Scheduling

## Overview
This Python-based virtual assistant pulls the features I love from Reclaim.ai and/or Motion, but without the pricetag. Further, additional features were also added to dynamically alter task scheduling based on the upcoming weather forecast, as I live with a chronic illness that is exacerbated significantly by hot weather. The virtual assistant is designed to help users manage their tasks and schedule efficiently. It integrates with multiple APIs, including Google Services (Sheets, Calendar), Todoist, and the Met Office's DataHub API, to streamline task management, automate scheduling, and enhance productivity.

## Key Features
1. Authentication with APIs  
  Google APIs: Authenticate with Google Sheets, and Google Calendar.  
  Todoist: Authenticate and retrieve tasks.  
  Met Office API: Access weather data to trigger weather-based rules.  
2. Weather-Based Alerts  
   Monitors weather forecasts to send alerts for hot weather.
3. Task Management  
Fetches and parses task data from Todoist, extracting details such as:  
    * Task type
    * Required energy level
    * Estimated task duration
    * Task importance
    * Proximity to task deadline

4. Meeting and Calendar Management  
Integrates with Google Calendar to handle Meetings with travel time or rest periods.  
Automatically creates 30 minutes of travel time for in-person meetings (adjustable).  
Automatically creates 15 minutes of screen-free time after virtual meetings (e.g., Zoom, Teams, Skype). This is potentially helpful for combatting the phenomenon of [Zoom Fatigue]() 
Calculates available time slots, avoiding scheduling conflicts with meetings and travel.  
Schedules personal tasks in personal slots and work tasks in work slots.  
Schedules tasks in suitable slots

5. Task Scheduling and Prioritization  
Calculates task priority based on:  
    * Required focus level
    * Estimated task duration
    * Task importance
    * Proximity to task deadline

Matches tasks to suitable time slots in your schedule based on:  
    * Task type    
    * Focus level    
    * Time slots not already occupied by meetings or travel/ screen-free time.    

## What this Virtual Assistant _does not_ currently do
These features need adding before this Virtual Assistant is considered to be fully working:  
  * Lacks a Graphical User Interface - entirely runs in the terminal, output only really seen in Google Calendar. 
  * No way to manually override scheduling behaviour ('Low Spoon Mode') - can currently botch it to completely stop scheduling by creating a multiday event in Google Calendar.
  * Timezone currently hardcoded as UTC, so does not yet handle BST.
     
These features are likely to appear in a later version:  
  * Does not currently understand the concept of [task switching](https://www.apa.org/topics/research/multitasking). Ideally, the virtual assistant would use the tags attached to each task to batch similar tasks together.


## Installation

To set up this virtual assistant, ensure you have Python installed (Python 3.7 or later). Clone this repository and install the required packages:

git clone https://github.com/kiri-thornalley/virtual-assistant.git
cd virtual-assistant
pip install -r requirements.txt

## Configuration

Google API Credentials: Follow the instructions on the Google Cloud Console to create OAuth 2.0 credentials and download the credentials.json file.

Todoist API Token: Obtain your API token from Todoist Developers.

Met Office API Key: Sign up for an API key from the Met Office.

## Usage

Run the main script to initiate the virtual assistant:

python main.py

This will prompt the authentication process and begin scheduling and task management.

License