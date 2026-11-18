UPDATE: 
Placed 3rd in N+1/Qualcomm Track for MadHacks26!

[View the DevPost](https://devpost.com/software/let-s-dig-in?_gl=1*14lemx8*_gcl_au*MTk4OTEyNTIyMS4xNzk0NzU3MTMz*_ga*MTczMTEzMjcxMC4xNzk0NzU3MTMz*_ga_0YHJK3Y10M*czE3OTUwMjU0MTMkbzUkZzEkdDE3OTUwMjU0ODkkajYwJGwwJGgw)

------------
Let's Dig In
Every meal is a checkup. A wrist-worn tracker that measures hand tremors and eating pace during meals, running entirely on an Arduino UNO Q.
Built at MadHacks 2026.

Why:
More than 10 million people worldwide live with Parkinson's (Parkinson's Foundation). Tremors change from day to day, but a doctor only sees a patient for a few minutes every few months, so one visit can miss the real trend.

Everyone eats every day. Let's Dig In uses each meal as a measurement, so changes show up over weeks and months.

What it does:
Starts on its own. A camera sees someone sit down, counts down 5 seconds, records the hand's resting position, and starts tracking. Getting up saves the meal.
Counts bites. Each lift of the hand to the mouth is counted and placed on a ring that shows the whole meal.
Detects tremors. It tells rhythmic shaking apart from ordinary movement. During a tremor the screen pulses red and that stretch of the meal is marked on the ring.
Checks in. If a tremor lasts more than 5 seconds, the screen asks "Are you okay?"
Tracks the trend. Each meal is saved with seconds per bite, tremor magnitude, and number of tremor episodes, then compared with the person's own baseline (their first three meals). A change of 25% or more raises a flag.

All sensing, analysis, and AI run on the board. Nothing is sent to the cloud.

Hardware:
Arduino UNO Q
Arduino Modulino Movement (accelerometer), connected to the Qwiic port and worn on the wrist
USB webcam
USB-C hub with power pass-through, plus a USB-C charger

How it works:
Wrist sensor ──> microcontroller ──> Bridge ──┐
                                              ├──> Python on the board ──> web dashboard
Webcam ──> Video Object Detection Brick ──────┘

Sensor. The board's microcontroller reads the accelerometer every 16 ms and sends each reading to the Linux side over Arduino's Bridge.
Tremor detection. Python runs a frequency analysis on the last 1.5 seconds of motion. A tremor is rhythmic, so most of its energy sits at one frequency between 3 and 8 Hz, while ordinary movement is spread across many. A tremor is reported only when the shaking is strong enough and rhythmic for several readings in a row.
Bite detection. The board tracks how far the wrist has tilted from its resting position. A tilt up past 35° and back down counts as one bite.
Camera. App Lab's Video Object Detection Brick runs an AI model on the board and reports when a person is in view. That starts and ends each meal.
Dashboard. The board hosts a web page with a live tremor trace, the meal ring, and meal-by-meal history. Any device on the same network can open it.

Repository layout:
arduinomadhacks/
├── app.yaml            App Lab app definition and Bricks
├── sketch/sketch.ino   Microcontroller: reads the sensor
├── python/main.py      Analysis, meal logic, camera, web server
└── assets/index.html   Dashboard

Running it
Wire the hardware: hub's host cable into the board, charger and webcam into the hub, sensor into the Qwiic port.
Put the board and your laptop on the same network. A phone hotspot works where campus Wi-Fi blocks devices from seeing each other.
Open the app in Arduino App Lab. It needs the WebUI - HTML and Video Object Detection Bricks.
Press Run and wait for "App started."
Open the Network URL printed in the console, which looks like http://<board-ip>:7000.

To run without the camera, set USE_CAMERA = False in main.py, remove the Video Object Detection Brick, and use the Start now and End now buttons.

Using the dashboard:
Sit down in view of the camera with your eating hand resting on the table.
After the countdown, eat. Bites appear on the ring and tremors show in red.
Get up. After 5 seconds out of view the meal is saved under Meal by meal.
Load simulated history adds eight clearly labeled simulated meals to show what a two-year trend looks like.
Settings

These are at the top of python/main.py.

Setting	Value	Meaning:
TREMOR_MIN	0.12	Minimum shaking strength (m/s²) to count as a tremor
RHYTHM_MIN	0.7	How rhythmic the shaking must be, from 0 to 1
BITE_UP_DEG	35	Wrist tilt that starts a bite
SIT_S	5	Seconds in view before a meal starts
AUTO_END_AWAY_S	5	Seconds out of view before a meal ends
CHANGE_FLAG	0.25	Change from baseline that raises a flag

Limitations:
This is a hackathon prototype, not a medical device. It tracks and flags changes. It does not diagnose.
The severity levels and thresholds are demo values, not clinical ones.
The long-term trend shown in demos uses simulated history.
Bite counting uses a wrist-tilt rule, not a trained model.
The sensor's cable is short, so the board sits close to the wrist.

What's next:
Testing with real patients over weeks and months
Plain-language reports a patient can share with their doctor
A smaller, watch-sized wearable
A phone in place of the webcam
A trained model for bite detection

Team:
Aarav, Arvind, Pranav, and Shravan

Built with

Arduino UNO Q, Arduino App Lab, Modulino Movement, Python, C++, JavaScript, HTML, CSS, Socket.IO
