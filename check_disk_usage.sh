#!/bin/bash

# Function to send email notification
send_email_notification() {
    MESSAGE_BODY="Filesystem usage is over 80%. The Purge has been triggered."
    EMAIL_SUBJECT="Alert: Filesystem Usage is more than 80%"
    FROM_EMAIL_ADDRESS="you email"  # Update with your Gmail address
    FRIENDLY_NAME="No Reply Test"
    SMTP_SERVER="smtp.gmail.com"
    SMTP_PORT="587"
    SMTP_USER="you email"  # Update with your Gmail address
    SMTP_PASS=""  # Update with your Gmail app password
    TO_EMAIL_ADDRESS=""  # Update with recipient's email address
    FILEPATH="purge.log"

    echo -e "Subject: $EMAIL_SUBJECT\n\n$MESSAGE_BODY" | mailx -a $FILEPATH -S smtp-use-starttls -S smtp-auth=login -S smtp=smtp://$SMTP_SERVER:$SMTP_PORT -S from="$FROM_EMAIL_ADDRESS($FRIENDLY_NAME)" -S smtp-auth-user=$SMTP_USER -S smtp-auth-password=$SMTP_PASS -S ssl-verify=ignore $TO_EMAIL_ADDRESS
}

# Function to log messages
log_message() {
    echo "$(date +"%Y-%m-%d %H:%M:%S") - $1" >> purge.log
}

DIRECTORY="/"

# Check if the directory exists
if [ ! -d "$DIRECTORY" ]; then
    echo "Error: Directory '$DIRECTORY' not found!" | tee -a purge.log
    exit 1
fi

# Calculate the disk usage in percentage
DISK_USAGE=$(df -hP "$DIRECTORY" | awk 'NR==2 {print $5}' | sed 's/%//')

# Log the current filesystem usage
log_message "Current filesystem usage: $DISK_USAGE%"

# Output the current filesystem usage with color coding
if [ "$DISK_USAGE" -gt 80 ]; then
    echo -e "Current filesystem usage: \033[0;31m$DISK_USAGE%\033[0m" | tee -a purge.log  # Red for error
    send_email_notification
elif [ "$DISK_USAGE" -gt 60 ]; then
    echo -e "Current filesystem usage: \033[38;5;202m$DISK_USAGE%\033[0m" | tee -a purge.log  # Yellow for warning
else
    echo -e "Current filesystem usage: \033[0;32m$DISK_USAGE%\033[0m" | tee -a purge.log  # Green for acceptable limits
fi

# Threshold for triggering the other script (80%)
THRESHOLD=80

# Check if disk usage exceeds the threshold
if [ "$DISK_USAGE" -gt "$THRESHOLD" ]; then
    echo "Filesystem usage is over 80%. Triggering Purge script..." | tee -a purge.log
    # Call your other script here
    cd ./ && /usr/bin/python3 purge.py
    # Send email notification
    send_email_notification
fi