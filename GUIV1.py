import tkinter as tk
from tkinter import scrolledtext
import ttkbootstrap as ttk
import os
from assistantV6 import (  # Import functions from your assistant code
    parse_personal_and_work_tasks,
    fetch_calendar_events,
    schedule_tasks,
    merge_scheduled_tasks,
    manage_calendar_events,
    authenticate_google_services,
)

# Function to adjust sliders to ensure total does not exceed 1.0
def adjust_sliders(changed_slider, all_sliders, value_labels):
    total = sum(slider.get() for slider in all_sliders)
    if total > 1.0:
        excess = total - 1.0
        for slider, label in zip(all_sliders, value_labels):
            if slider != changed_slider:
                current_value = slider.get()
                adjustment = min(current_value, excess)
                slider.set(current_value - adjustment)
                label.config(text=f"{slider.get():.2f}")
                excess -= adjustment
                if excess <= 0:
                    break

# Function to update slider label and adjust other sliders
def on_slider_change(slider, label, all_sliders, value_labels):
    value = slider.get()
    slider.set(round(value, 2))  # Ensure precise rounding to 2 decimal places
    label.config(text=f"{slider.get():.2f}")
    adjust_sliders(slider, all_sliders, value_labels)

# Function to handle adding a task
def add_task():
    task_name = task_name_entry.get()
    priority = priority_combo.get()
    urgency = urgency_combo.get()
    if task_name and priority and urgency:
        task_list.insert("", "end", values=(task_name, priority, urgency))
        task_name_entry.delete(0, tk.END)

# Function to adjust algorithm weightings
def update_weightings():
    duration_weight = duration_slider.get()
    energy_weight = energy_slider.get()
    impact_weight = impact_slider.get()
    urgency_weight = urgency_slider.get()
    print(f"Updated Weights - Duration: {duration_weight:.2f}, Energy: {energy_weight:.2f}, Impact: {impact_weight:.2f} Urgency: {urgency_weight:.2f}")

# Function to delete token.json to reset Google authentication
def delete_token():
    if os.path.exists("token.json"):
        os.remove("token.json")
    else:
        print("The file does not exist")

# Create main window
root = ttk.Window(themename='minty') 
# Choose from: Light - cosmo, flatly, journal, litera, lumen, minty, pulse, sandstone, united, yeti, morph, simplex, cerculean
# Choose from: Dark - solar, superhero, darkly, cyborg, vapor 
root.title("Virtual Assistant")
#root.geometry("800x500")  # Width x Height

# Create a Notebook widget for tabs
notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True)

# -------- Tab 0: Home ------
home_tab = ttk.Frame(notebook)
notebook.add(home_tab, text="Home")

home_heading_label = ttk.Label(home_tab, text="Welcome to your virtual assistant - VANessa", font=("Helvetica", 16, "bold"))
home_heading_label.pack(pady=10)
home_intro_text = ttk.Label(home_tab, text = "Here is more text", font=("Arial", 12))
home_intro_text.pack(pady=5)

# -------- Tab 1: Task Entry --------
task_entry_tab = ttk.Frame(notebook)
notebook.add(task_entry_tab, text="Enter Tasks")

task_entry_label = ttk.Label(task_entry_tab, text="Enter New Task", font=("Helvetica", 16, "bold"))
task_entry_label.pack(pady=10)

form_frame = ttk.Frame(task_entry_tab)
form_frame.pack(pady=10)

# Task Name Entry
task_name_label = ttk.Label(form_frame, text="Task Name:")
task_name_label.grid(row=0, column=0, padx=10, pady=5)
task_name_entry = ttk.Entry(form_frame, width=30)
task_name_entry.grid(row=0, column=1, padx=10, pady=5)

# Priority Dropdown
priority_label = ttk.Label(form_frame, text="Priority (1-5):")
priority_label.grid(row=1, column=0, padx=10, pady=5)
priority_combo = ttk.Combobox(form_frame, values=[1, 2, 3, 4, 5], width=5)
priority_combo.grid(row=1, column=1, padx=10, pady=5)

# Urgency Dropdown
urgency_label = ttk.Label(form_frame, text="Urgency (1-5):")
urgency_label.grid(row=2, column=0, padx=10, pady=5)
urgency_combo = ttk.Combobox(form_frame, values=[1, 2, 3, 4, 5], width=5)
urgency_combo.grid(row=2, column=1, padx=10, pady=5)

# Add Task Button
add_button = ttk.Button(task_entry_tab, bootstyle = "secondary-outline", text="Add Task", command=add_task)
add_button.pack(pady=20)

# -------- Tab 2: View Today's Schedule --------
schedule_tab = ttk.Frame(notebook)
notebook.add(schedule_tab, text="Today's Schedule")

schedule_label = ttk.Label(schedule_tab, text="Today's Schedule", font=("Helvetica", 16, "bold"))
schedule_label.pack(pady=10)

# Treeview to Display Scheduled Tasks
task_list = ttk.Treeview(schedule_tab, columns=("Task", "Priority", "Urgency"), show="headings", height=15)
task_list.heading("Task", text="Task Name")
task_list.heading("Priority", text="Priority")
task_list.heading("Urgency", text="Urgency")
task_list.pack(fill="both", expand=True, padx=20, pady=10)

# -------- Tab 3: Adjust Algorithm Weightings --------
weighting_tab = ttk.Frame(notebook)
notebook.add(weighting_tab, text="Settings")

# Configure columns to expand
weighting_tab.grid_columnconfigure(0, weight=1)  # Column 0 (Labels) fixed size
weighting_tab.grid_columnconfigure(1, weight=3)  # Column 1 (Sliders) expands
weighting_tab.grid_columnconfigure(2, weight=1)  # Column 2 (Value Labels) fixed size

frame = ttk.LabelFrame(weighting_tab, text=" Adjust Algorithm Weightings ", bootstyle="dark")  # Frame title and style
frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew") 

# Sliders for Algorithm Weights
sliders = []  # To store all sliders for adjustment logic
value_labels = []  # To store all slider value labels

# Time Weight Slider
duration_label = ttk.Label(frame, text="Task Duration Weight:")
duration_label.grid(row=1, column=0, padx=10)

duration_slider = ttk.Scale(frame, from_=0, to=1, orient="horizontal")
duration_slider.grid(row=1, column=1, padx=10, sticky="ew")  # Expand horizontally
duration_slider.set(0.2)

duration_value_label = ttk.Label(frame, text="0.2")
duration_value_label.grid(row=1, column=2, padx=10)

duration_slider.bind(
    "<ButtonRelease-1>", lambda e: on_slider_change(duration_slider, duration_value_label, sliders, value_labels)
)
sliders.append(duration_slider)
value_labels.append(duration_value_label)

# Energy Weight Slider
energy_label = ttk.Label(frame, text="Energy Weight:")
energy_label.grid(row=2, column=0, padx=10)

energy_slider = ttk.Scale(frame, from_=0, to=1, orient="horizontal")
energy_slider.grid(row=2, column=1, padx=10, sticky="ew")
energy_slider.set(0.4)

energy_value_label = ttk.Label(frame, text="0.4")
energy_value_label.grid(row=2, column=2, padx=10)

energy_slider.bind(
    "<ButtonRelease-1>", lambda e: on_slider_change(energy_slider, energy_value_label, sliders, value_labels)
)
sliders.append(energy_slider)
value_labels.append(energy_value_label)

# Impact Weight Slider
impact_label = ttk.Label(frame, text="Impact Weight:")
impact_label.grid(row=3, column=0, padx=10)

impact_slider = ttk.Scale(frame, from_=0, to=1, orient="horizontal")
impact_slider.grid(row=3, column=1, padx=10, sticky="ew")
impact_slider.set(0.3)

impact_value_label = ttk.Label(frame, text="0.3")
impact_value_label.grid(row=3, column=2, padx=10)

impact_slider.bind(
    "<ButtonRelease-1>", lambda e: on_slider_change(impact_slider, impact_value_label, sliders, value_labels)
)
sliders.append(impact_slider)
value_labels.append(impact_value_label)

# Urgency Weight Slider
urgency_label = ttk.Label(frame, text="Urgency Weight:")
urgency_label.grid(row=4, column=0, padx=10)

urgency_slider = ttk.Scale(frame, from_=0, to=1, orient="horizontal")
urgency_slider.grid(row=4, column=1, padx=10, sticky="ew")
urgency_slider.set(0.1)

urgency_value_label = ttk.Label(frame, text="0.1")
urgency_value_label.grid(row=4, column=2, padx=10)

urgency_slider.bind(
    "<ButtonRelease-1>", lambda e: on_slider_change(urgency_slider, urgency_value_label, sliders, value_labels)
)
sliders.append(urgency_slider)
value_labels.append(urgency_value_label)

# Update Button
update_button = ttk.Button(frame, text="Update Weightings", command=update_weightings)
update_button.grid(row=5, column=0, columnspan=3, pady=20)

frame_2 = ttk.LabelFrame(weighting_tab, text=" The Danger Zone ", bootstyle="dark")  # Frame title and style
frame_2.grid(row=1, column=0, padx=10, pady=10, sticky="ew") 
##### The Danger Zone
# Low Spoon Mode Checkbutton
spoon_mode = ttk.Checkbutton(frame_2, text="Low Spoon Mode", bootstyle="round-toggle")
spoon_mode.grid(row=5, column=0, columnspan=3, padx=10, pady=10)

reset_token_label = ttk.Label(frame_2, text="Reset token - here is more text")
reset_token_label.grid(row=6, column=0, columnspan=3, padx=10)
reset_token_button = ttk.Button(frame_2, text="Reset Token", bootstyle="danger", command=delete_token)
reset_token_button.grid(row=7, column=0, columnspan=3, padx=10, pady=10)

root.mainloop()