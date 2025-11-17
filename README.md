# Elmdale Weather Alerts Lambda

An AWS Lambda function that monitors weather forecasts and sends email/SMS alerts to manage bathroom heaters at the Elmdale property. The system uses a finite state machine (FSM) to track weather conditions and only sends alerts when transitioning between states.

## Overview

This Lambda function:
- Fetches weather forecasts from OpenWeather One Call 3.0 API
- Analyzes hourly and daily forecasts for freeze risk
- Maintains state (COLD/WARM) in DynamoDB
- Sends email alerts via AWS SES when state transitions occur
- Sends SMS alerts via AWS SNS when state transitions occur
- Uses conservative logic: defaults to COLD state if conditions are unclear

## Features

### State Machine (FSM)
- **COLD State**: Freeze risk detected or conditions not warm enough
- **WARM State**: No freeze risk and sustained warm conditions
- **State Persistence**: Current state stored in DynamoDB
- **Transition Alerts**: Alerts only sent when state changes (not on every run)

### Weather Analysis
- **Hourly Forecast Check**: Analyzes next N hours (default: 12) for immediate freeze risk
- **Daily Forecast Check**: Analyzes next N days (default: 2) for sustained warm conditions
- **Freeze Threshold**: Default 32°F - temperatures at or below trigger COLD state
- **Warm Threshold**: Default 35°F - minimum overnight lows must be at or above for WARM state

### Alert Types
- **Cold Alert** (❄️): Sent when transitioning to COLD state - instructs to turn ON heaters
- **Warm Alert** (☀️): Sent when transitioning to WARM state - instructs to turn OFF heaters
- **Test Alert** (🧪): Status email showing current state and forecast analysis

### Communication Channels
- **Email**: Via AWS SES (Amazon Simple Email Service)
- **SMS**: Via AWS SNS (Amazon Simple Notification Service)

## Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `WEATHER_API_KEY` | OpenWeather API key | `abc123...` |
| `SES_SENDER` | Verified SES sender email | `alerts@example.com` |
| `RECIPIENTS` | Comma-separated recipient emails | `user1@example.com,user2@example.com` |
| `STATE_TABLE_NAME` | DynamoDB table name for state storage | `elmdale-weather-state` |

### Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LAT` | Latitude for weather location | `38.3736` |
| `LON` | Longitude for weather location | `-96.6447` |
| `HOURS_AHEAD` | Hours to check for freeze risk | `12` |
| `FREEZE_THRESHOLD_F` | Freeze threshold in Fahrenheit | `32.0` |
| `WARM_CLEAR_DAYS` | Days to check for warm conditions | `2` |
| `WARM_THRESHOLD_F` | Warm threshold in Fahrenheit | `35.0` |
| `SNS_TOPIC_ARN` | SNS topic ARN for SMS (optional) | (not set) |

## State Machine Logic

### State Determination

1. **Check for Freeze Risk**: Analyze hourly forecast for temperatures ≤ `FREEZE_THRESHOLD_F`
   - If any hours found → **COLD state**

2. **Check for Warm Conditions** (only if no freeze risk):
   - Analyze daily forecast for next `WARM_CLEAR_DAYS`
   - All days must have `temp.min ≥ WARM_THRESHOLD_F`
   - If all conditions met → **WARM state**

3. **Default**: If conditions unclear → **COLD state** (conservative)

### State Transitions

- **First Run** (`last_state == None`):
  - Sets initial state based on forecast
  - Sends alert email and SMS for initial state

- **State Unchanged** (`current_state == last_state`):
  - No alerts sent
  - State not updated

- **State Changed** (`current_state != last_state`):
  - Updates stored state in DynamoDB
  - Sends appropriate alert (COLD or WARM)
  - Sends SMS notification

## Test Modes

The Lambda accepts an event with a `mode` field to control behavior. Test modes do **not** modify the stored state.

### 1. TEST Mode
**Event**: `{"mode": "TEST"}`  
**File**: `test-events/test-status.json`

**Behavior**:
- Fetches real weather data
- Analyzes forecast and determines state
- Sends status email showing:
  - Last stored state
  - Forecast-derived state
  - Hourly forecast summary
  - Daily forecast (10 days)
  - All thresholds and configuration
- Sends test SMS
- **Does NOT change stored state**

**Use Case**: Check current weather conditions and system status without triggering state changes.

### 2. TEST_SMS_ONLY Mode
**Event**: `{"mode": "TEST_SMS_ONLY"}`  
**File**: `test-events/test-sms-only.json`

**Behavior**:
- Sends test SMS only
- No email sent
- No state changes
- Does not require weather data

**Use Case**: Verify SMS/SNS integration is working.

### 3. TEST_COLD Mode
**Event**: `{"mode": "TEST_COLD"}`  
**File**: `test-events/test-cold.json`

**Behavior**:
- Creates simulated cold weather data (temps below freeze threshold)
- Sends **Cold Alert email** (❄️) with simulated freeze conditions
- Shows what a cold alert looks like
- **Does NOT change stored state**

**Use Case**: Test the cold alert email format and content without waiting for actual cold weather.

### 4. TEST_WARM Mode
**Event**: `{"mode": "TEST_WARM"}`  
**File**: `test-events/test-warm.json`

**Behavior**:
- Creates simulated warm weather data (temps above thresholds)
- Sends **Warm Alert email** (☀️) with simulated warm conditions
- Shows what a warm alert looks like
- **Does NOT change stored state**

**Use Case**: Test the warm alert email format and content without waiting for actual warm weather.

### 5. NORMAL Mode (Default)
**Event**: `{"mode": "NORMAL"}` or `{}`  
**File**: `test-events/test-normal.json`

**Behavior**:
- Fetches real weather data
- Analyzes forecast
- Updates state if changed
- Sends alerts on state transitions
- **Modifies stored state**

**Use Case**: Production operation - actual monitoring and alerting.

## Email Alerts

### Cold Alert Email (❄️)
**Subject**: `❄️ Cold Alert: Turn ON Bathroom Heaters`

**Content**:
- Action required: Turn ON bathroom heaters
- What was checked: Hourly forecast analysis, freeze threshold
- Forecast details: Lowest temperature, freeze period
- Why action is required: Freeze risk explanation

**Triggered**: When transitioning to COLD state

### Warm Alert Email (☀️)
**Subject**: `☀️ Warm Alert: Turn OFF Bathroom Heaters`

**Content**:
- Action required: Turn OFF bathroom heaters
- What was checked: Hourly and daily forecast analysis, both thresholds
- Check results: No freeze risk, sustained warm conditions
- Why action is safe: Explanation of safe conditions

**Triggered**: When transitioning to WARM state

### Test/Status Email (🧪)
**Subject**: `🧪 Test Alert: Elmdale Weather Monitor Status`

**Content**:
- TEST MODE indicator
- What was checked: Analysis details
- Current system state: Last stored vs forecast-derived
- Short-term forecast results
- Daily forecast (10 days)
- Note that state was not modified

**Triggered**: When using TEST mode

## SMS Alerts

SMS alerts are sent via AWS SNS when state transitions occur in NORMAL mode.

- **Cold Alert SMS**: Freeze alert with temperature and time range
- **Warm Alert SMS**: Warm-clear alert with conditions
- **Test SMS**: Simple test message

**Note**: SMS requires `SNS_TOPIC_ARN` environment variable to be set.

## Test Cases

### Test Case 1: Initial State - Cold Conditions
**Scenario**: First run with freeze risk in forecast  
**Expected**:
- State set to COLD
- Cold alert email sent
- Cold alert SMS sent
- State stored in DynamoDB

### Test Case 2: Initial State - Warm Conditions
**Scenario**: First run with no freeze risk and warm conditions  
**Expected**:
- State set to WARM
- Warm alert email sent
- Warm alert SMS sent
- State stored in DynamoDB

### Test Case 3: State Transition - COLD to WARM
**Scenario**: Stored state is COLD, forecast shows warm conditions  
**Expected**:
- State updated to WARM
- Warm alert email sent
- Warm alert SMS sent
- State updated in DynamoDB

### Test Case 4: State Transition - WARM to COLD
**Scenario**: Stored state is WARM, forecast shows freeze risk  
**Expected**:
- State updated to COLD
- Cold alert email sent
- Cold alert SMS sent
- State updated in DynamoDB

### Test Case 5: No State Change
**Scenario**: Stored state matches forecast-derived state  
**Expected**:
- No alerts sent
- State not updated
- Lambda returns success with "State unchanged" message

### Test Case 6: TEST Mode
**Scenario**: Event with `{"mode": "TEST"}`  
**Expected**:
- Status email sent with current conditions
- Test SMS sent
- State NOT modified
- Useful for checking system without triggering alerts

### Test Case 7: TEST_COLD Mode
**Scenario**: Event with `{"mode": "TEST_COLD"}`  
**Expected**:
- Cold alert email sent with simulated data
- State NOT modified
- Useful for testing cold alert format

### Test Case 8: TEST_WARM Mode
**Scenario**: Event with `{"mode": "TEST_WARM"}`  
**Expected**:
- Warm alert email sent with simulated data
- State NOT modified
- Useful for testing warm alert format

### Test Case 9: Conservative Default
**Scenario**: Forecast unclear (e.g., no daily data, borderline temps)  
**Expected**:
- Defaults to COLD state (conservative)
- Cold alert sent if transitioning from WARM

### Test Case 10: No Hourly Data
**Scenario**: Weather API returns no hourly data  
**Expected**:
- In TEST mode: Status email with "UNKNOWN" state
- In NORMAL mode: Returns error, no state change

## File Structure

```
elmdale-weather-alerts/
├── lambda_trigger.py          # Main Lambda function
├── test-events/               # Test event JSON files
│   ├── test-normal.json      # Normal operation
│   ├── test-status.json      # TEST mode
│   ├── test-sms-only.json    # TEST_SMS_ONLY mode
│   ├── test-cold.json        # TEST_COLD mode
│   └── test-warm.json        # TEST_WARM mode
└── README.md                  # This file
```

## Usage

### Testing Locally
You can test the Lambda function using AWS SAM, Serverless Framework, or by invoking it directly with test events.

### Deploying
Deploy to AWS Lambda with:
- Python 3.9+ runtime
- Required environment variables set
- IAM permissions for:
  - SES (SendEmail)
  - DynamoDB (GetItem, PutItem)
  - SNS (Publish) - if using SMS

### Invoking Test Events
Use the test event JSON files in `test-events/` directory:
- AWS Console: Create test event and paste JSON
- AWS CLI: `aws lambda invoke --function-name <name> --payload file://test-events/test-status.json`
- SAM: `sam local invoke -e test-events/test-status.json`

## Key Functions

### Weather Functions
- `get_weather()`: Fetches weather from OpenWeather API
- `find_freeze_hours()`: Finds hours with freeze risk
- `find_warm_clear_days()`: Checks for sustained warm conditions

### Email Functions
- `send_freeze_email()`: Sends cold alert email
- `send_warm_ok_email()`: Sends warm alert email
- `send_status_email()`: Sends test/status email

### SMS Functions
- `send_freeze_sms()`: Sends cold alert SMS
- `send_warm_ok_sms()`: Sends warm alert SMS
- `send_test_sms()`: Sends test SMS

### State Functions
- `get_last_state()`: Retrieves state from DynamoDB
- `set_last_state()`: Updates state in DynamoDB

## Notes

- The system uses **Central Time** (America/Chicago) for all time displays
- State transitions are the only time alerts are sent (prevents spam)
- Conservative approach: defaults to COLD if conditions unclear
- Test modes never modify stored state
- SMS is optional (only sent if SNS_TOPIC_ARN is configured)

## Troubleshooting

### No Alerts Received
- Check state hasn't changed (alerts only on transitions)
- Verify email addresses in RECIPIENTS
- Check SES sender is verified
- Review CloudWatch logs for errors

### State Not Updating
- Check DynamoDB table permissions
- Verify STATE_TABLE_NAME is correct
- Review CloudWatch logs for errors

### SMS Not Working
- Verify SNS_TOPIC_ARN is set
- Check SNS topic permissions
- Review CloudWatch logs for errors

### Weather API Errors
- Verify WEATHER_API_KEY is valid
- Check API quota/limits
- Verify LAT/LON coordinates
- Review CloudWatch logs for HTTP errors

