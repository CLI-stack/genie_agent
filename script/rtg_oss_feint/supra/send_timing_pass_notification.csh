#!/bin/tcsh
# Script to send email notification when timing pass report files are found
# Usage: send_timing_pass_notification.csh <source_dir> <tag> <tile_name> <tile_dir> <target_name> <pass_number> <file_path>

if ($#argv != 7) then
    echo "Usage: $0 <source_dir> <tag> <tile_name> <tile_dir> <target_name> <pass_number> <file_path>"
    exit 1
endif

set source_dir = $1
set tag = $2
set tile_name = $3
set tile_dir = $4
set target_name = $5
set pass_number = $6
set file_path = $7

echo "========================================================================"
echo "Sending timing pass notification email..."
echo "Tag: $tag"
echo "Tile: $tile_name"
echo "Target: $target_name"
echo "Pass: $pass_number"
echo "File: $file_path"
echo "========================================================================"

# Extract timing from QoR report
set qor_file = "${tile_dir}/rpts/${target_name}/${target_name}.pass_${pass_number}.proc_qor.rpt.gz"
set uclk_wns = "N/A"
set uclk_tns = "N/A"
set uclk_nvp = "N/A"
set clk_period = "N/A"
set has_uclk = 0
set r2r_timing_file = "/tmp/r2r_timing_${tag}_pass${pass_number}_$$.tmp"

if (-f "$qor_file") then
    echo "Extracting timing from: $qor_file"

    # First try to extract UCLK timing
    set uclk_check = `zcat "$qor_file" 2>/dev/null | grep -c "Timing Path Group  'UCLK'"`

    if ($uclk_check > 0) then
        set has_uclk = 1
        echo "Found UCLK timing path group"

        # Extract UCLK timing block:
        # Timing Path Group  'UCLK'
        # ----------------------------------------
        # Levels of Logic:                     26
        # Critical Path Length:            409.10
        # Critical Path Slack:            -173.20
        # Critical Path Clk Period:        274.60
        # Total Negative Slack:        -923986.98
        # No. of Violating Paths:           21354

        set uclk_wns = `zcat "$qor_file" 2>/dev/null | grep -A6 "Timing Path Group  'UCLK'" | grep "Critical Path Slack:" | awk '{print $NF}'`
        set uclk_tns = `zcat "$qor_file" 2>/dev/null | grep -A7 "Timing Path Group  'UCLK'" | grep "Total Negative Slack:" | awk '{print $NF}'`
        set uclk_nvp = `zcat "$qor_file" 2>/dev/null | grep -A8 "Timing Path Group  'UCLK'" | grep "No. of Violating Paths:" | awk '{print $NF}'`
        set clk_period = `zcat "$qor_file" 2>/dev/null | grep -A6 "Timing Path Group  'UCLK'" | grep "Critical Path Clk Period:" | awk '{print $NF}'`

        if ("$uclk_wns" == "") set uclk_wns = "N/A"
        if ("$uclk_tns" == "") set uclk_tns = "N/A"
        if ("$uclk_nvp" == "") set uclk_nvp = "N/A"
        if ("$clk_period" == "") set clk_period = "N/A"

        echo "Extracted UCLK - WNS: $uclk_wns, TNS: $uclk_tns, NVP: $uclk_nvp, Period: $clk_period"
    else
        echo "UCLK not found - extracting all R2R/SYN timing path groups"

        # Extract all R2R and SYN_ timing groups (r2r, R2R, SYN_nonIP, SYN_I2R, SYN_R2O, etc.)
        rm -f $r2r_timing_file

        # Get all matching group names (use awk with single quote delimiter)
        set r2r_groups = `zcat "$qor_file" |& grep -iE "Timing Path Group.*(r2r|SYN_)" | awk -F"'" '{print $2}'`

        # Pattern prefix for grep (built separately to avoid quote issues)
        set search_pattern = "Timing Path Group  '"

        foreach r2r_group ($r2r_groups)
            set grp_wns = `zcat "$qor_file" |& grep -A6 "${search_pattern}${r2r_group}'" | grep "Critical Path Slack:" | awk '{print $NF}'`
            set grp_tns = `zcat "$qor_file" |& grep -A7 "${search_pattern}${r2r_group}'" | grep "Total Negative Slack:" | awk '{print $NF}'`
            set grp_nvp = `zcat "$qor_file" |& grep -A8 "${search_pattern}${r2r_group}'" | grep "No. of Violating Paths:" | awk '{print $NF}'`
            set grp_period = `zcat "$qor_file" |& grep -A6 "${search_pattern}${r2r_group}'" | grep "Critical Path Clk Period:" | awk '{print $NF}'`

            if ("$grp_wns" == "") set grp_wns = "N/A"
            if ("$grp_tns" == "") set grp_tns = "N/A"
            if ("$grp_nvp" == "") set grp_nvp = "N/A"
            if ("$grp_period" == "") set grp_period = "N/A"

            echo "${r2r_group},${grp_wns},${grp_tns},${grp_nvp},${grp_period}" >> $r2r_timing_file
            echo "  $r2r_group - WNS: $grp_wns, TNS: $grp_tns, NVP: $grp_nvp, Period: $grp_period"
        end
    endif
else
    echo "WARNING: QoR report not found: $qor_file"
endif

# Create notification spec file
set notify_spec = "${source_dir}/data/${tag}_timing_pass${pass_number}_notify.spec"
rm -f $notify_spec

# Write notification message
echo "#text#" >> $notify_spec
echo "NOTIFICATION: Timing Pass ${pass_number} Report Generated" >> $notify_spec
echo "" >> $notify_spec
echo "A timing pass report file has been generated for ${target_name}:" >> $notify_spec
echo "" >> $notify_spec

# Add timing summary table
if ($has_uclk == 1) then
    # UCLK timing summary
    echo "#table#" >> $notify_spec
    echo "Tile,Target,Pass,UCLK WNS (ps),UCLK TNS (ps),Violating Paths,Clock Period (ps)" >> $notify_spec
    echo "${tile_name},${target_name},Pass ${pass_number},${uclk_wns},${uclk_tns},${uclk_nvp},${clk_period}" >> $notify_spec
    echo "#table end#" >> $notify_spec
else
    # R2R timing groups summary
    echo "#table#" >> $notify_spec
    echo "Timing Path Group,WNS (ps),TNS (ps),Violating Paths,Clock Period (ps)" >> $notify_spec
    if (-f $r2r_timing_file) then
        cat $r2r_timing_file >> $notify_spec
    endif
    echo "#table end#" >> $notify_spec
    echo "" >> $notify_spec
    echo "#text#" >> $notify_spec
    echo "Note: UCLK timing path group not found. Showing all R2R/SYN timing groups." >> $notify_spec
endif

echo "" >> $notify_spec
echo "#text#" >> $notify_spec
echo "QoR Report: ${target_name}.pass_${pass_number}.proc_qor.rpt.gz" >> $notify_spec
echo "Timing Report: report_timing.pass_${pass_number}.rpt.sum.sort_slack.endpts.gz" >> $notify_spec
echo "" >> $notify_spec
echo "Location: ${tile_dir}/rpts/${target_name}/" >> $notify_spec
echo "" >> $notify_spec

# Get VTO name
source ${source_dir}/csh/env.csh
set vto = `cat assignment.csv | grep "vto," | head -n 1 | awk -F ',' '{print $2}' | sed 's/\r//g'`

# Add signature
echo "" >> $notify_spec
echo "Thanks," >> $notify_spec
echo "$vto (Genie AI Agent)" >> $notify_spec
echo "" >> $notify_spec
echo "#line#" >> $notify_spec

# Convert spec to HTML - first create a temporary spec with proper email format
set email_spec = "${source_dir}/data/${tag}_timing_pass${pass_number}_email.spec"
rm -f $email_spec

# Create email-formatted spec
echo "#text#" > $email_spec
echo "[TIMING REPORT] Pass ${pass_number} Report Generated for $target_name" >> $email_spec
echo "" >> $email_spec
cat $notify_spec >> $email_spec

# Add quote section for email threading
echo "#text#" >> $email_spec
echo "-----Original Message-----" >> $email_spec

# Get original email body if available
set mail_body = `python ${source_dir}/py/readTask.py --tasksModelFile tasksModel.csv --tag $tag --item mailBody 2>/dev/null | tail -1`
if ("$mail_body" != "" && "$mail_body" != "None") then
    echo "$mail_body" >> $email_spec
endif

# Convert to HTML
set notify_html = "${source_dir}/data/${tag}_timing_pass${pass_number}_notify.html"
python ${source_dir}/py/spec2Html.py --spec $email_spec --html $notify_html

# Send email directly using formail + sendmail
echo "Sending notification email directly..."

# Get ALL recipients from assignment.csv (manager + all debuggers)
set recipients = ""

# Get manager
set manager = `cat assignment.csv | grep "manager," | head -n 1 | awk -F ',' '{print $2}' | sed 's/\r//g'`
if ("$manager" != "") then
    set recipients = "$manager"
endif

# Get ALL debuggers (not just the first one)
foreach debugger_line (`cat assignment.csv | grep "debugger," | awk -F ',' '{print $2}' | sed 's/\r//g'`)
    if ("$recipients" != "") then
        set recipients = "${recipients},${debugger_line}"
    else
        set recipients = "$debugger_line"
    endif
end

# Get original sender from tasksModel if available
set sender_email = `python ${source_dir}/py/readTask.py --tasksModelFile tasksModel.csv --tag $tag --item sender 2>/dev/null | tail -1`
if ("$sender_email" != "" && "$sender_email" != "None") then
    if ("$recipients" != "") then
        set recipients = "${recipients},${sender_email}"
    else
        set recipients = "$sender_email"
    endif
endif

# Get subject
set subject = `python ${source_dir}/py/readTask.py --tasksModelFile tasksModel.csv --tag $tag --item subject 2>/dev/null | tail -1`
if ("$subject" == "" || "$subject" == "None") then
    set subject = "TileBuilder Timing Report Notification"
endif

# Remove duplicate email addresses from recipients list
if ("$recipients" != "") then
    echo "$recipients" | tr ',' '\n' | sort -u > /tmp/recipients_unique_$$.tmp
    set recipients = `cat /tmp/recipients_unique_$$.tmp | tr '\n' ',' | sed 's/,$//'`
    rm -f /tmp/recipients_unique_$$.tmp
endif

echo "DEBUG: recipients = $recipients"
echo "DEBUG: subject = $subject"

# Send email using formail + sendmail
if ("$recipients" != "") then
    cat $notify_html | formail -I "To: $recipients" -I "From: $vto (Genie AI Agent)" -I "MIME-Version: 1.0" -I "Content-type: text/html; charset=utf-8" -I "Subject: [TIMING REPORT] Re: $subject - Pass ${pass_number} Generated" | /sbin/sendmail -oi $recipients

    set mail_status = $status
    if ($mail_status == 0) then
        echo "[OK] Timing pass notification email sent successfully to: $recipients"
    else
        echo "[ERROR] Failed to send timing pass notification email (exit code: $mail_status)"
    endif
else
    echo "[ERROR] WARNING: No recipients found - email not sent"
endif

# Cleanup temp files
rm -f $r2r_timing_file

echo "========================================================================"
