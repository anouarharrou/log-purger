#!/bin/bash

PURGE_DIR="/home/harry/purger"
LOG_FILE="$PURGE_DIR/cron_logs/cron_log_$(date +'%Y-%m-%d').log"
CRITICAL_USAGE=2
FILESYSTEM="/dev/sdc"
SMTP_HOST="smtp.gmail.com"
SMTP_PORT=587
CONFIG_FILE="$PURGE_DIR/purge_config.json"
START_TIME=$(date)

# Extract hostname and nickname
NICKNAME=$(hostname -I | awk '{print $1}')
HOSTNAME=$(echo "$HOSTNAME")

# Gmail credentials
SMTP_USER=""  # Replace with your SMTP EMAIL/USERNAME
SMTP_PASS=""  # Replace with your SMTP Password

# Email details
MAIL_FROM="$SMTP_USER"
MAIL_TO="receiver email"

# Function to send email notifications
send_email() {
    local subject=$1
    local message=$2
    local temp_file=$(mktemp)

    # Add headers to improve inbox delivery
    echo -e "Subject: $subject\nContent-Type: text/html; charset=UTF-8\nFrom: $MAIL_FROM\nTo: $MAIL_TO\nReply-To: $MAIL_FROM\nMIME-Version: 1.0\nX-Priority: 1\nX-Mailer: Bash Script" > "$temp_file"
    echo -e "\n<html><body><h2 style='color:#2E8B57;'>$subject</h2><p>$message</p><footer><p>Sent from <strong>DevOps Monitoring</strong> 🛠️</p></footer></body></html>" >> "$temp_file"

    # Send email using curl with STARTTLS
    local curl_output
    curl_output=$(curl -v --url "smtp://$SMTP_HOST:$SMTP_PORT" \
        --ssl-reqd \
        --mail-from "$MAIL_FROM" \
        --mail-rcpt "$MAIL_TO" \
        --upload-file "$temp_file" \
        --user "$SMTP_USER:$SMTP_PASS" 2>&1)

    local curl_status=$?

    # Debugging information
    if [ $curl_status -ne 0 ]; then
        echo "Email sending failed. Debug information:" >> "$LOG_FILE"
        echo "$curl_output" >> "$LOG_FILE"
        echo "Curl exit status: $curl_status" >> "$LOG_FILE"
    else
        echo "Email sent successfully. Debug information:" >> "$LOG_FILE"
        echo "$curl_output" >> "$LOG_FILE"
    fi

    # Clean up the temporary file
    rm -f "$temp_file"
}

# Create log directory and file if they don't exist
mkdir -p "$(dirname "$LOG_FILE")" || {
    send_email "🚨 Script Failure: Log Directory Creation Failed" "Failed to create log directory. Please investigate the issue."
    exit 1
}
touch "$LOG_FILE" || {
    send_email "🚨 Script Failure: Log File Creation Failed" "Failed to create log file. Please check the permissions and try again."
    exit 1
}

{
    echo "#######################################################################"
    echo "CRON JOB STARTED AT $START_TIME"
    echo "#######################################################################"
    echo "CHECK DISK USAGE"

    # Get disk usage
    usage=$(df -h | awk -v fs="$FILESYSTEM" '$1 == fs {print $5}' | sed 's/%//')
    echo "FILESYSTEM $FILESYSTEM USAGE : $usage%"

    # Handle missing usage value
    if [ -z "$usage" ]; then
        echo "Error: Disk usage could not be determined for $FILESYSTEM." >> "$LOG_FILE"
        send_email "🚨 Script Failure: Disk Usage Not Found" "The filesystem $FILESYSTEM is not present or the disk usage could not be determined. Please investigate the issue."
        exit 1
    fi

    if [ "$usage" -ge "$CRITICAL_USAGE" ]; then
        echo "USAGE IS GREATER OR EQUAL TO $CRITICAL_USAGE% ==> RUN PURGE SCRIPT"

        cd "$PURGE_DIR" || {
            send_email "🚨 Script Failure: Directory Change Failed" "Failed to change directory to $PURGE_DIR. Please check the directory."
            exit 1
        }

        echo "#######################################################################"
        echo "STARTING PURGE SCRIPT EXECUTION AT $(date)"
        echo "#######################################################################"

        if ! python3 $PURGE_DIR/purge.py; then
            echo "#######################################################################"
            echo "PURGE SCRIPT FAILED AT $(date)"
            echo "#######################################################################"
            send_email "❌ Script Failure: PURGE Execution Failed" "The purge.py script failed to execute. Please check the logs in $LOG_FILE for more details."
            exit 1
        fi

        echo "#######################################################################"
        echo "PURGE SCRIPT COMPLETED SUCCESSFULLY AT $(date)"
        echo "#######################################################################"

        if bucket_name=$(jq -r '.config.bucket' "$CONFIG_FILE"); then
            echo "Bucket name retrieved successfully: $bucket_name"
        else
            send_email "❌ Script Failure: Bucket Name Retrieval Failed" "Failed to retrieve bucket name from $CONFIG_FILE after running purge.py."
            exit 1
        fi

        if path=$(jq -r '.config.project' "$CONFIG_FILE"); then
            echo "Path name retrieved successfully: $path"
        else
            send_email "❌ Script Failure: Path Name Retrieval Failed" "Failed to retrieve path name from $CONFIG_FILE after running purge.py."
            exit 1
        fi

        if service=$(jq -r '.services[0].service' "$CONFIG_FILE"); then
            echo "Service name retrieved successfully: $service"
        else
            send_email "❌ Script Failure: Service Name Retrieval Failed" "Failed to retrieve Service name from $CONFIG_FILE after running purge.py."
            exit 1
        fi

        END_TIME=$(date)
        send_email "✅ Script Success: Execution Completed" "
        <ul>
            <li><strong>Start Time:</strong> $START_TIME ⏳</li>
            <li><strong>End Time:</strong> $END_TIME ⏰</li>
            <li><strong>Service Name:</strong> $service 🔧</li>
            <li><strong>Bucket Name:</strong> $bucket_name 🗂️</li>
            <li><strong>Path:</strong> $path 📁</li>
            <li><strong>Hostname:</strong> $HOSTNAME 👤</li>
            <li><strong>SERVER:</strong> $NICKNAME 🖥️</li>
        </ul>
        <p>Everything went smoothly! 🎉</p>"
    else
        echo "USAGE IS LESS THAN $CRITICAL_USAGE% ==> DO NOTHING"
    fi

    echo "#######################################################################"
    echo "CRON JOB ENDED AT $(date)"
    echo "#######################################################################"

} >> "$LOG_FILE" 2>&1 || {
    send_email "❌ Script Failure: Unknown Error" "An unknown error occurred while running the script. Check the logs in $LOG_FILE for details."
    exit 1
}
