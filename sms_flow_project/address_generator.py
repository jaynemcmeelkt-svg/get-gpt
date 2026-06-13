import json
import random
import os

class LocalAddressGenerator:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.us_data = self._load_json('usData.json')
        self.us_real = self._load_json('usRealAddresses.json')
        self.names_data = self._load_json('namesData.json')

    def _load_json(self, filename: str) -> dict:
        path = os.path.join(self.data_dir, filename)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def generate_us_address(self, selected_state: str = 'RANDOM') -> dict:
        """
        生成高信任度的美国真实地址
        :param selected_state: 州代码，如 'OR' (俄勒冈), 'DE' (特拉华) 或 'RANDOM'
        """
        # 1. 确定州 (State)
        state_code = selected_state
        if selected_state == 'RANDOM':
            states_available = self.us_real.get('states_available', [])
            if states_available:
                state_code = random.choice(states_available)
            else:
                state_code = random.choice(list(self.us_real['data'].keys()))

        state_info = self.us_data['states'].get(state_code)
        if not state_info:
            raise ValueError(f"State {state_code} not found in usData.json")

        # 2. 随机抽取该州的一条真实街道、城市、邮编数据 (OpenStreetMap 真实采样)
        real_addresses = self.us_real['data'].get(state_code, [])
        if not real_addresses:
            raise ValueError(f"No real address data for state {state_code}")
        
        addr_record = random.choice(real_addresses)
        street = addr_record['street']
        city = addr_record['city']
        zip_code = addr_record['zip']
        county = addr_record.get('county', 'N/A')

        # 3. 生成配套姓名与性别
        gender = 'Male' if random.random() > 0.5 else 'Female'
        name_group = self.names_data['nameGroups']['western']
        
        first_names = name_group['first']['male'] if gender == 'Male' else name_group['first']['female']
        first_name = random.choice(first_names)
        last_name = random.choice(name_group['last'])

        # 4. 生成配套电话号码 (使用该州的真实区号)
        area_code = random.choice(state_info['area_codes'])
        phone = f"+1 ({area_code}) {random.randint(200, 999)}-{random.randint(1000, 9999)}"

        # 5. 生成配套邮箱
        email = f"{first_name.lower()}.{last_name.lower()}{random.randint(10, 99)}@example.com"

        # 6. 拼接完整地址
        state_name_en = state_info['name']['en']
        full_address = f"{street}, {city}, {state_name_en} {zip_code}"

        return {
            "name": f"{first_name} {last_name}",
            "first_name": first_name,
            "last_name": last_name,
            "gender": gender,
            "phone": phone,
            "email": email,
            "street": street,
            "city": city,
            "county": county,
            "state": state_name_en,
            "state_code": state_code,
            "zip": zip_code,
            "full_address": full_address,
            "country": "US"
        }
