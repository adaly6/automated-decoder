from flask import Flask, request, Response, render_template, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, WebDriverException
import pandas as pd
import webbrowser
import re
from io import BytesIO
from threading import Timer
import time
import os
import signal

app = Flask(__name__)

def open_browser():
    webbrowser.open_new('http://127.0.0.1:5000/')

def scrape_vin_data(vin_numbers):
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    driver = webdriver.Chrome(options=chrome_options)
    driver.get("https://vpic.nhtsa.dot.gov/decoder/")
    df = pd.DataFrame(columns=['VIN', 'Vehicle Type', 'Body Class', 'Weight'])

    for vin in vin_numbers:
        vin_input = driver.find_element(By.ID, "VIN")
        vin_input.clear()
        vin_input.send_keys(vin)
        decode_button = driver.find_element(By.ID, "btnSubmit")
        decode_button.click()
        time.sleep(2) 

        try:
            vehicle_type = driver.find_element(By.XPATH, "/html/body/div[2]/div[3]/div[2]/div/div[2]/div[2]/div[1]/p[3]").text

            body_class = driver.find_element(By.XPATH, "/html/body/div[2]/div[3]/div[2]/div/div[2]/div[2]/div[1]/p[7]").text

            if "INCOMPLETE VEHICLE" in vehicle_type:
                weight = driver.find_element(By.XPATH, "/html/body/div[2]/div[4]/div/div[2]/div[1]/div").text
            elif "TRAILER" in vehicle_type:
                weight = driver.find_element(By.XPATH, "/html/body/div[2]/div[4]/div/div[2]/p[2]").text
            elif "MOTORCYCLE" in vehicle_type:
                weight = driver.find_element(By.XPATH, "/html/body/div[2]/div[4]/div/div[2]/p[2]").text
            elif "BUS" in vehicle_type:
                weight = driver.find_element(By.XPATH, "/html/body/div[2]/div[4]/div/div[2]/div[2]/div[2]").text
            else:
                weight = driver.find_element(By.XPATH, "/html/body/div[2]/div[4]/div/div[2]/div[2]/div[1]").text
                                                        
        except (NoSuchElementException, WebDriverException) as e:
            vehicle_type = "Check VIN"
            body_class = ""
            weight = ""
        
        new_row = pd.DataFrame({'VIN': [vin], 'Vehicle Type': [vehicle_type], 'Body Class': [body_class], 'Weight': [weight]})
        df = pd.concat([df, new_row], ignore_index=True)

        driver.refresh()        

    driver.quit()
    return df

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/shutdown', methods=['POST'])
def shutdown():
    os.kill(os.getpid(), signal.SIGTERM)
    return jsonify({'status': 'shutting down'})

@app.route('/submit', methods=['POST'])
def submit():
    vin_input = request.form['vin_numbers']
    vin_numbers = vin_input.splitlines() 
    df = scrape_vin_data(vin_numbers)

    df['Vehicle Type'] = df['Vehicle Type'].str.replace('Vehicle Type: ', '')
    df['Body Class'] = df['Body Class'].str.replace('Body Class: ', '')
    df['Weight'] = df['Weight'].astype(str)
    df['Weight'] = df['Weight'].str.replace('Gross Vehicle Weight Rating: ', '')
    df['Weight'] = df['Weight'].str.replace(r'.*:\s+', '', regex=True)
    df['Weight'] = df['Weight'].str.replace(r'\(.*?\)', '', regex=True).str.strip()

    df.loc[df['Vehicle Type'].str.contains("Vehicle Type:"), 'Vehicle Type'] = "Invalid VIN"
    df.loc[df['Vehicle Type'] == "Invalid VIN", ['Body Class', 'Weight']] = ""

    df.loc[df['Body Class'].str.contains("Body Class:"), 'Body Class'] = "--"
    df.loc[df['Weight'].str.contains("Gross Vehicle Weight Rating:"), 'Weight'] = "--"

    def weight_mean(weight_range):
        if pd.isna(weight_range) or weight_range == "--":
            return None
        weight_bounds = re.findall(r'\d{1,3}(?:,\d{3})*', weight_range)
        weight_bounds = [int(weight.replace(",", "")) for weight in weight_bounds]
        if len(weight_bounds) == 1:
            return weight_bounds[0]
        elif len(weight_bounds) == 2:
            return sum(weight_bounds) / 2
        else:
            return None
        
    df['Weight_mean'] = df['Weight'].apply(weight_mean)
    
    def classify_vehicle(row):
        if row['Vehicle Type'] == "Invalid VIN":
            return "NA/Trailer"
        elif row['Vehicle Type'] == "TRAILER" or row['Body Class'] == "Trailer":
            return "Trailer"
        elif row['Body Class'] == "Truck-Tractor":
            return "Truck-Tractor"
        elif row['Body Class'] == "Cargo Van":
            return "Cargo Van"
        elif (row['Vehicle Type'] == "TRUCK" and row['Weight_mean'] < 10000) or (row['Body Class'] == "Incomplete" and row['Weight_mean'] < 10000) or (row['Body Class'] == "Pickup" and row['Weight_mean'] < 10000):
            return "LT"
        elif row['Vehicle Type'] == "MOTORCYCLE" or row['Vehicle Type'] == "LOW SPEED VEHICLE (LSV)":
            return "Motorcycle"
        elif row['Vehicle Type'] == "PASSENGER CAR" or row['Vehicle Type'] == "MULTIPURPOSE PASSENGER VEHICLE (MPV)":
            return "PP"
        elif 10001 <= row['Weight_mean'] <= 20000 or (row['Body Class'] == "Truck" and 10001 <= row['Weight_mean'] <= 20000):
            return "MT"
        elif row['Body Class'] == "Van":
            return "Van"
        elif 20001 <= row['Weight_mean'] <= 33000 or (row['Body Class'] == "Truck" and 20001 <= row['Weight_mean'] <= 33000):
            return "HT"
        elif row['Weight_mean'] == 33001 and row['Body Class'] != "Truck-Tractor":
            return "EHT"
        else:
            return "OtherNA"

    df['Classification'] = df.apply(classify_vehicle, axis=1)

    invalid_vins = df.loc[df['Vehicle Type'] == "Invalid VIN", 'VIN'].tolist()
    max_rows = len(df)
    df['Invalid VINs'] = invalid_vins + [None] * (max_rows - len(invalid_vins))

    df = df.drop('Weight_mean', axis=1)

    csv_string = df.to_csv(index=False)

    mem = BytesIO()
    mem.write(csv_string.encode('utf-8'))
    mem.seek(0)

    return Response(mem, mimetype='text/csv', headers={"Content-disposition": "attachment; filename=vehicle_data.csv"})

if __name__ == "__main__":
    webbrowser.open_new('http://127.0.0.1:5000/')
    app.run(debug=False, use_reloader=False)