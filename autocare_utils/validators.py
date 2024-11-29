from datetime import datetime, timedelta

class AppointmentValidator:
    @staticmethod
    def validate_car_info(make, model, year):
        """Validate car information."""
        current_year = datetime.now().year
        
        if not make or len(make.strip()) < 2:
            return False, "Car make must be at least 2 characters"
            
        if not model or len(model.strip()) < 2:
            return False, "Car model must be at least 2 characters"
            
        try:
            year = int(year)
            if year < 1900 or year > current_year + 1:
                return False, f"Car year must be between 1900 and {current_year + 1}"
        except ValueError:
            return False, "Invalid year format"
            
        return True, "Valid car information"

    @staticmethod
    def validate_appointment_time(date_str, time_str):
        """Validate appointment date and time."""
        try:
            # Parse date
            appointment_date = datetime.strptime(date_str, '%Y-%m-%d')
            
            # Check if date is in the past
            if appointment_date.date() < datetime.now().date():
                return False, "Appointment date cannot be in the past"
                
            # Check if date is too far in the future (e.g., 3 months)
            max_future_date = datetime.now() + timedelta(days=90)
            if appointment_date.date() > max_future_date.date():
                return False, "Appointment cannot be scheduled more than 3 months in advance"
                
            # Validate business hours (9 AM to 4 PM)
            valid_times = ['09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00']
            if time_str not in valid_times:
                return False, "Invalid appointment time. Please select a time between 9 AM and 4 PM"
                
            return True, "Valid appointment time"
            
        except ValueError:
            return False, "Invalid date format"

    @staticmethod
    def validate_service_type(service_type):
        """Validate service type."""
        valid_services = ['oil-change', 'tire-rotation', 'brake-service', 
                         'general-inspection', 'repair']
        
        if service_type not in valid_services:
            return False, f"Invalid service type. Must be one of: {', '.join(valid_services)}"
            
        return True, "Valid service type"
